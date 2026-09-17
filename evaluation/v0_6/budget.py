"""MVP v0.6 Step 02：预算守卫与只追加账本（覆盖适配器内部重试）。

设计要点（任务书 / 协议第 5 节）：

- **请求发出前持久化预留**：每次图片调用前先向 `budget_ledger.jsonl` 追加一条
  `reserve`（最坏尝试数 `1 + Settings.http_max_retries`、最坏成本），再调用 Provider；
  调用结束追加 `settle`。进程中断留下的未结算预留按**已消耗的未知成本**处理。
- **内部重试不可观察**：`OpenAIImageProvider` 在 adapter 内重试至多
  `Settings.http_max_retries` 次，评测层看不到每次内部尝试；因此按该次调用的
  **最大尝试成本**预留与记账（`cost_basis` 如实标注为最坏上界），绝不低估。
- **耗尽即停**：`can_reserve` 在四个上限（总额 / 请求数 / 尝试数 / 单次最坏成本）
  任一不满足时返回原因，runner 在发出请求前停止，不“先调用再补记”。
- **unknown 不自动重发**：`unknown_reservations` 暴露未结算/超时预留，runner 据此写
  人工决定并跳过续跑。

账本只追加、不重写；同 run 的续跑复用同一账本继续累计。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .records import BudgetLedgerEntry, utc_now

LEDGER_FILENAME = "budget_ledger.jsonl"


class BudgetError(RuntimeError):
    """预算配置或记账错误。"""


class BudgetExceededError(BudgetError):
    """预留会突破冻结上限（在请求发出前抛出）。"""


class BudgetPolicy(BaseModel):
    """冻结的预算策略（由 G1 配置构造；离线 Fake 演练允许价格为 0/上限为 None）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: 币种代码；None 表示尚未确认（真实运行被门禁拒绝）。
    currency: str | None = None
    #: 总金额上限（币种最小单位整数）；None 表示未确认。
    max_total_minor: int | None = Field(default=None, ge=0)
    #: 图片请求数上限（外层调用次数）。
    max_requests: int | None = Field(default=None, ge=0)
    #: 底层尝试数上限（含 adapter 内部重试的最坏情况）。
    max_attempts: int | None = Field(default=None, ge=0)
    #: 单次图片请求单价（币种最小单位整数）；None 表示价格未核实。
    unit_price_minor: int | None = Field(default=None, ge=0)
    #: 价格来源（如 `run_config.json#budget` 或 G1 记录）；必须如实可追溯。
    price_source: str | None = None
    #: 单次调用最多内部重试次数（= Settings.http_max_retries）。
    max_internal_retries: int = Field(default=0, ge=0)
    #: 成功调用的记账口径说明。
    cost_basis: str = "worst_case_upper_bound_unobservable_internal_retries"
    #: 未知成本是否按已消耗计（协议要求 True）。
    unknown_counts_as_consumed: bool = True

    @property
    def worst_case_attempts(self) -> int:
        """一次图片调用可能产生的底层尝试数上界。"""
        return 1 + self.max_internal_retries

    @property
    def worst_case_cost_minor(self) -> int:
        if self.unit_price_minor is None:
            return 0
        return self.unit_price_minor * self.worst_case_attempts

    @property
    def is_real_ready(self) -> bool:
        """真实启动所需的最小预算完整性（缺一即拒绝真实入口）。"""
        return (
            self.currency is not None
            and self.max_total_minor is not None
            and self.max_requests is not None
            and self.max_attempts is not None
            and self.unit_price_minor is not None
            and self.price_source is not None
        )

    def missing_fields(self) -> list[str]:
        missing = [
            name
            for name, value in (
                ("currency", self.currency),
                ("max_total_minor", self.max_total_minor),
                ("max_requests", self.max_requests),
                ("max_attempts", self.max_attempts),
                ("unit_price_minor", self.unit_price_minor),
                ("price_source", self.price_source),
            )
            if value is None
        ]
        return missing


class BudgetSnapshot(BaseModel):
    """账本重放后的只读汇总。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reserved_requests: int = Field(ge=0)
    reserved_attempts: int = Field(ge=0)
    charged_minor: int = Field(ge=0)
    #: 已结算且状态已知（ok / failed）的记账额。
    charged_known_minor: int = Field(ge=0, default=0)
    #: 已结算但结果未知（unknown）的记账额（最坏上界口径）。
    charged_unknown_minor: int = Field(ge=0, default=0)
    outstanding_minor: int = Field(ge=0)
    settled_ok: int = Field(ge=0)
    settled_failed: int = Field(ge=0)
    settled_unknown: int = Field(ge=0)
    unsettled: int = Field(ge=0)


class BudgetLedger:
    """只追加预算账本（`reserve` → 调用 → `settle`）。"""

    def __init__(self, path: Path, policy: BudgetPolicy, *, clock=None) -> None:
        self._path = Path(path)
        self._policy = policy
        self._clock = clock or utc_now
        self._entries: list[BudgetLedgerEntry] = []
        self._reservations: dict[str, BudgetLedgerEntry] = {}
        self._settles: dict[str, BudgetLedgerEntry] = {}
        if self._path.is_file():
            self._replay()

    # -- 只读 ---------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def policy(self) -> BudgetPolicy:
        return self._policy

    @property
    def entries(self) -> list[BudgetLedgerEntry]:
        return list(self._entries)

    @property
    def reservations(self) -> list[BudgetLedgerEntry]:
        """全部预留（含已结算）；用于崩溃窗口恢复（settle 后、记录落盘前）。"""
        return list(self._reservations.values())

    def settle_entry(self, reservation_id: str) -> BudgetLedgerEntry | None:
        """某预留的结算行（未结算返回 None）。"""
        return self._settles.get(reservation_id)

    def _replay(self) -> None:
        for line_number, raw in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            try:
                entry = BudgetLedgerEntry.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001 - 账本损坏必须显式失败
                raise BudgetError(f"{self._path}:{line_number} is not a ledger entry: {exc}") from exc
            self._entries.append(entry)
            if entry.event == "reserve":
                self._reservations[entry.reservation_id] = entry
            else:
                self._settles[entry.reservation_id] = entry

    def snapshot(self) -> BudgetSnapshot:
        reserved_requests = len(self._reservations)
        reserved_attempts = sum(
            entry.worst_case_attempts or 0 for entry in self._reservations.values()
        )
        charged = sum(
            entry.charged_cost_minor or 0 for entry in self._settles.values()
        )
        charged_unknown = sum(
            (entry.charged_cost_minor or 0)
            for entry in self._settles.values()
            if entry.status == "unknown"
        )
        outstanding = sum(
            (entry.worst_case_cost_minor or 0)
            for reservation_id, entry in self._reservations.items()
            if reservation_id not in self._settles
        )
        statuses = [entry.status for entry in self._settles.values()]
        return BudgetSnapshot(
            reserved_requests=reserved_requests,
            reserved_attempts=reserved_attempts,
            charged_minor=charged,
            charged_known_minor=charged - charged_unknown,
            charged_unknown_minor=charged_unknown,
            outstanding_minor=outstanding,
            settled_ok=statuses.count("ok"),
            settled_failed=statuses.count("failed"),
            settled_unknown=statuses.count("unknown"),
            unsettled=sum(
                1 for reservation_id in self._reservations if reservation_id not in self._settles
            ),
        )

    @property
    def unknown_reservations(self) -> list[BudgetLedgerEntry]:
        """超时/中断等结果未知的预留（含未结算的崩溃残留）。"""
        unknown: list[BudgetLedgerEntry] = []
        for reservation_id, entry in self._reservations.items():
            settle = self._settles.get(reservation_id)
            if settle is None or settle.status == "unknown":
                unknown.append(entry)
        return unknown

    def unknown_pair_keys(self) -> set[str]:
        return {entry.pair_key for entry in self.unknown_reservations}

    # -- 预留判定 -----------------------------------------------------------

    def can_reserve(self) -> tuple[bool, str]:
        """是否还能发下一次图片调用（在最坏成本口径下）。"""
        snapshot = self.snapshot()
        policy = self._policy
        worst_attempts = policy.worst_case_attempts
        worst_cost = policy.worst_case_cost_minor
        if policy.max_requests is not None and snapshot.reserved_requests + 1 > policy.max_requests:
            return False, (
                f"request cap reached ({snapshot.reserved_requests}/{policy.max_requests})"
            )
        if policy.max_attempts is not None and snapshot.reserved_attempts + worst_attempts > policy.max_attempts:
            return False, (
                f"attempt cap would be exceeded "
                f"({snapshot.reserved_attempts}+{worst_attempts}/{policy.max_attempts})"
            )
        if policy.max_total_minor is not None:
            projected = snapshot.charged_minor + snapshot.outstanding_minor + worst_cost
            if projected > policy.max_total_minor:
                return False, (
                    f"cost cap would be exceeded "
                    f"(charged={snapshot.charged_minor} outstanding={snapshot.outstanding_minor} "
                    f"next_worst={worst_cost} cap={policy.max_total_minor})"
                )
        return True, ""

    # -- 写 -----------------------------------------------------------------

    def _append(self, entry: BudgetLedgerEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._entries.append(entry)
        if entry.event == "reserve":
            self._reservations[entry.reservation_id] = entry
        else:
            self._settles[entry.reservation_id] = entry

    def reserve(self, *, reservation_id: str, pair_key: str, attempt_index: int) -> BudgetLedgerEntry:
        """在请求发出前持久化最坏预留；超出上限抛 `BudgetExceededError`。"""
        if reservation_id in self._reservations:
            raise BudgetError(f"reservation {reservation_id!r} already exists")
        allowed, reason = self.can_reserve()
        if not allowed:
            raise BudgetExceededError(reason)
        entry = BudgetLedgerEntry(
            event="reserve",
            reservation_id=reservation_id,
            pair_key=pair_key,
            attempt_index=attempt_index,
            created_at=self._clock(),
            worst_case_attempts=self._policy.worst_case_attempts,
            worst_case_cost_minor=self._policy.worst_case_cost_minor,
            currency=self._policy.currency,
        )
        self._append(entry)
        return entry

    def settle(
        self,
        reservation_id: str,
        *,
        status: str,
        charged_cost_minor: int,
        cost_source: str,
    ) -> BudgetLedgerEntry:
        """事后如实结算；未知状态按已消耗计入。"""
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise BudgetError(f"cannot settle unknown reservation {reservation_id!r}")
        if reservation_id in self._settles:
            raise BudgetError(f"reservation {reservation_id!r} is already settled")
        if charged_cost_minor < 0:
            raise BudgetError("charged_cost_minor must be >= 0")
        entry = BudgetLedgerEntry(
            event="settle",
            reservation_id=reservation_id,
            pair_key=reservation.pair_key,
            attempt_index=reservation.attempt_index,
            created_at=self._clock(),
            charged_cost_minor=charged_cost_minor,
            cost_source=cost_source,
            currency=self._policy.currency,
            status=status,  # type: ignore[arg-type]
        )
        self._append(entry)
        return entry


def budget_policy_from_config(config: dict, *, max_internal_retries: int) -> BudgetPolicy:
    """从 Step 01 `run_config.json` 的 `budget` 段构造策略。

    草案配置中金额/请求/尝试上限均为 `null`：离线 Fake 演练允许该状态（成本恒 0），
    真实入口由 `paired_runner.real_run_blockers` 显式拒绝。
    """
    budget = config.get("budget") or {}
    currency = budget.get("currency")
    max_total = budget.get("max_total_amount")
    max_requests = budget.get("max_requests")
    max_attempts = budget.get("max_attempts")
    price_source = budget.get("per_request_price_source")
    # 草案价格不在 config 中；G1 会通过同一策略对象注入 `unit_price_minor`。
    unit_price = budget.get("unit_price_minor")
    return BudgetPolicy(
        currency=currency if isinstance(currency, str) else None,
        max_total_minor=int(max_total) if isinstance(max_total, (int, float)) else None,
        max_requests=int(max_requests) if isinstance(max_requests, (int, float)) else None,
        max_attempts=int(max_attempts) if isinstance(max_attempts, (int, float)) else None,
        unit_price_minor=int(unit_price) if isinstance(unit_price, (int, float)) else None,
        price_source=price_source if isinstance(price_source, str) else None,
        max_internal_retries=max_internal_retries,
        unknown_counts_as_consumed=bool(budget.get("unknown_cost_requests_counted_as_consumed", True)),
    )


def read_ledger_entries(path: Path) -> list[BudgetLedgerEntry]:
    """只读读取账本（用于测试与诊断重放）。"""
    if not Path(path).is_file():
        return []
    entries: list[BudgetLedgerEntry] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if raw.strip():
            entries.append(BudgetLedgerEntry.model_validate_json(raw))
    return entries


__all__ = [
    "LEDGER_FILENAME",
    "BudgetError",
    "BudgetExceededError",
    "BudgetPolicy",
    "BudgetSnapshot",
    "BudgetLedger",
    "budget_policy_from_config",
    "read_ledger_entries",
]
