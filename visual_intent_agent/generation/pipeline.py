"""Step 08 GenerationPipeline：确认 → 编译 → 生成 → 落盘 → GenerationArtifact → 复核。

冻结流程（ARCHITECTURE.md 4「Step 08」；任务书「Workflow 规则」1～6）：

    generate(session_id):
        快照必须 WAITING_CONFIRMATION 且存在有效确认（`is_confirmation_valid`）
        → transition GENERATING
        → PromptEngine.compile(PromptCompileRequest)     # 其内部复核确认
        → ImageProvider.generate
        → 字节写入 {output_dir}/{generation_id}/image_{i}.png
        → append_generation_artifact(refs={"prompt_artifact_id": ...})
        → transition WAITING_REVIEW

    retry(session_id, generation_id):
        仅 FAILED 状态；加载原 GenerationArtifact 的 prompt_artifact_id，
        用**同一 PromptArtifact** 重新调用 Provider（**不得重新 compile**）；
        新 generation_id，历史不覆盖。原 GenerationArtifact 不存在时（纯 Provider 失败
        不写 Artifact，Rev.2 裁定 1）回退到 `get_latest_prompt_artifact(session_id)`
        ——即该次失败 generate 刚编译落库的原 PromptArtifact；会话从未编译过 Prompt
        仍抛 `generation.artifact_not_found`。两条路径都必须通过
        `is_confirmation_valid(prompt_artifact.based_on_confirmation_id)`，否则
        `generation.no_valid_confirmation`（状态不变、不调用 Provider）。

失败语义（任务书 Workflow 规则 4；第 5.6 节）：

- `ProviderError`：结构化日志 + transition FAILED，**不写任何 Artifact**，异常原样向上抛；
- `PromptCompilationError` / compile 内部 `RepositoryError`（Bundle / Realization /
  Prompt 写入失败）/ 输出落盘失败 / 落库或迁移失败：同样转为 FAILED 并向上抛，
  绝不停留在 GENERATING（GENERATING → FAILED 是已冻结迁移）；原错误 code 与异常
  因果保留，写库失败绝不伪装成知识无命中；
- 门禁失败（状态不对 / 无有效确认）：在 transition 之前拒绝，状态保持不变。

边界（任务书「Adapter 边界」「禁止范围」）：

- 本类**不修改 Intent、不重新编译 Prompt**（generate 的 compile 是流程的一部分，
  retry 绝不 compile）；不判断图片质量、不做多模型路由、不实现 Reference Image；
- 生成调用只把 `prompt` / `size` / `model` 三个字段交给 Provider；
- output 只是本地下载件，不是 RealizationState 的事实来源。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from visual_intent_agent.config import PROJECT_ROOT
from visual_intent_agent.domain.identifiers import new_id
from visual_intent_agent.persistence import (
    InvalidStateTransitionError,
    Repository,
    RepositoryError,
    StoredArtifact,
    WorkflowState,
)
from visual_intent_agent.prompt_engine import (
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptEngine,
)
from visual_intent_agent.providers.errors import ProviderError
from visual_intent_agent.providers.image import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
)

from .models import (
    GENERATION_ARTIFACT_NOT_FOUND,
    GENERATION_ID_PREFIX,
    GENERATION_INVALID_STATE,
    GENERATION_NO_VALID_CONFIRMATION,
    GENERATION_OUTPUT_WRITE_FAILED,
    GENERATION_SESSION_MISMATCH,
    OUTPUT_FILE_NAME_TEMPLATE,
    GenerationArtifact,
    GenerationError,
    OutputRef,
)

#: 结构化日志的事件名（日志中不出现 key / 完整临时 URL）。
GENERATION_FAILED_EVENT = "generation.failed"

_LOGGER = logging.getLogger(__name__)


class GenerationPipeline:
    """生成闭环用例（唯一入口；确定性代码，唯一外部调用是 Provider）。"""

    def __init__(
        self,
        repo: Repository,
        prompt_engine: PromptEngine,
        image_provider: ImageProvider,
        output_dir: Path,
    ) -> None:
        self._repo = repo
        self._prompt_engine = prompt_engine
        self._image_provider = image_provider
        self._output_dir = Path(output_dir)

    # -- 用例 --------------------------------------------------------------

    def generate(self, session_id: str) -> GenerationArtifact:
        """从当前确认出发完成一次生成；失败抛 `ProviderError` / `GenerationError`。"""
        snapshot = self._repo.get_current_session_snapshot(session_id)
        if snapshot.workflow_state is not WorkflowState.WAITING_CONFIRMATION:
            raise GenerationError(
                GENERATION_INVALID_STATE,
                f"session {session_id!r} is in {snapshot.workflow_state.value}; "
                "generation is only allowed in WAITING_CONFIRMATION",
            )
        confirmation_id = snapshot.latest_confirmation_id
        if not confirmation_id or not self._repo.is_confirmation_valid(confirmation_id):
            raise GenerationError(
                GENERATION_NO_VALID_CONFIRMATION,
                "generation requires a valid confirmation that binds the current "
                "intent/execution revision; ask the user to confirm the current intent first",
            )

        self._repo.transition_state(session_id, WorkflowState.GENERATING)
        try:
            prompt_artifact = self._prompt_engine.compile(
                PromptCompileRequest(
                    session_id=session_id,
                    confirmation_id=confirmation_id,
                )
            )
        except PromptCompilationError as exc:
            self._log_and_mark_failed(
                session_id,
                reason_code=exc.code,
                detail=str(exc),
            )
            raise
        except RepositoryError as exc:
            # compile 内部的 Bundle / Realization / Prompt 写入失败：同样复用 FAILED
            # 失败记录与迁移机制，绝不停留在 GENERATING。原 `persistence.*` code 与
            # 异常因果原样保留（bare raise），不伪装成知识无命中或编译失败。
            self._log_and_mark_failed(
                session_id,
                reason_code=exc.code,
                detail="failed to persist compile output (knowledge bundle / realization / prompt)",
            )
            raise
        return self._generate_from_prompt_artifact(session_id, prompt_artifact)

    def retry(
        self, session_id: str, generation_id: str | None = None
    ) -> GenerationArtifact:
        """在 FAILED 状态下用**同一** PromptArtifact 重试；新 generation_id，历史不覆盖。

        省略 generation_id 时显式重试本会话最新的已落库 PromptArtifact，供未产生
        GenerationArtifact 的 Provider 失败恢复使用；调用方无需从日志获取或伪造 ID。

        原 GenerationArtifact 不存在时（纯 Provider 失败按契约不写 Artifact）回退到本会话
        最新一条 PromptArtifact（`get_latest_prompt_artifact`，Rev.2 裁定 1）；回退只读取
        **已落库**的 PromptArtifact，绝不重新 compile。
        """
        snapshot = self._repo.get_current_session_snapshot(session_id)
        if snapshot.workflow_state is not WorkflowState.FAILED:
            raise GenerationError(
                GENERATION_INVALID_STATE,
                f"session {session_id!r} is in {snapshot.workflow_state.value}; "
                "retry is only allowed in FAILED",
            )

        prompt_artifact = self._load_retry_prompt_artifact(session_id, generation_id)
        if not self._repo.is_confirmation_valid(prompt_artifact.based_on_confirmation_id):
            raise GenerationError(
                GENERATION_NO_VALID_CONFIRMATION,
                f"the PromptArtifact reused by retry of {generation_id!r} is bound to a confirmation "
                "that no longer matches the current intent/execution revision; the user "
                "must confirm again before retrying",
            )

        self._repo.transition_state(session_id, WorkflowState.GENERATING)
        return self._generate_from_prompt_artifact(session_id, prompt_artifact)

    # -- 内部实现 ----------------------------------------------------------

    def _generate_from_prompt_artifact(
        self, session_id: str, prompt_artifact: PromptArtifact
    ) -> GenerationArtifact:
        """调用 Provider、落盘、保存 Artifact、进入 WAITING_REVIEW（generate/retry 共用）。"""
        generation_id = new_id(GENERATION_ID_PREFIX)
        request = ImageGenerationRequest(
            prompt=prompt_artifact.prompt,
            size=prompt_artifact.parameters.size,
            model=prompt_artifact.target_model,
        )
        try:
            result = self._image_provider.generate(request)
        except ProviderError as exc:
            self._log_and_mark_failed(
                session_id,
                generation_id=generation_id,
                prompt_artifact_id=prompt_artifact.prompt_artifact_id,
                reason_code=exc.code,
                retryable=exc.retryable,
                status_code=exc.status_code,
                provider_request_id=exc.provider_request_id,
                detail=str(exc),
            )
            raise

        try:
            output_refs = self._write_outputs(generation_id, result)
        except OSError as exc:
            self._log_and_mark_failed(
                session_id,
                generation_id=generation_id,
                prompt_artifact_id=prompt_artifact.prompt_artifact_id,
                reason_code=GENERATION_OUTPUT_WRITE_FAILED,
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise GenerationError(
                GENERATION_OUTPUT_WRITE_FAILED,
                f"failed to write generated image bytes for generation {generation_id!r}",
            ) from exc

        artifact = GenerationArtifact(
            generation_id=generation_id,
            session_id=session_id,
            prompt_artifact_id=prompt_artifact.prompt_artifact_id,
            target_model=prompt_artifact.target_model,
            model_version=result.model,
            parameters=prompt_artifact.parameters,
            seed=result.seed,
            output_refs=output_refs,
            provider_request_id=result.provider_request_id,
        )
        try:
            self._repo.append_generation_artifact(
                generation_id,
                session_id,
                {"prompt_artifact_id": prompt_artifact.prompt_artifact_id},
                artifact.model_dump_json(),
            )
        except RepositoryError as exc:
            self._log_and_mark_failed(
                session_id,
                generation_id=generation_id,
                prompt_artifact_id=prompt_artifact.prompt_artifact_id,
                reason_code=exc.code,
                detail="failed to persist the GenerationArtifact",
            )
            raise

        try:
            self._repo.transition_state(session_id, WorkflowState.WAITING_REVIEW)
        except (InvalidStateTransitionError, RepositoryError) as exc:
            # Artifact 已保存但未能进入复核：留在 FAILED（GENERATING → FAILED 合法），
            # 这样 retry 可以复用同一 PromptArtifact，历史仍可审计。
            self._log_and_mark_failed(
                session_id,
                generation_id=generation_id,
                prompt_artifact_id=prompt_artifact.prompt_artifact_id,
                reason_code=exc.code,
                detail="the GenerationArtifact was saved but WAITING_REVIEW could not be entered",
            )
            raise
        return artifact

    # -- 落盘 / 读取 -------------------------------------------------------

    def _write_outputs(
        self, generation_id: str, result: ImageGenerationResult
    ) -> list[OutputRef]:
        """把 Provider 返回的字节写入 `{output_dir}/{generation_id}/image_{i}.png`。"""
        target_dir = self._output_dir / generation_id
        target_dir.mkdir(parents=True, exist_ok=True)
        output_refs: list[OutputRef] = []
        for index, image in enumerate(result.images, start=1):
            file_path = target_dir / OUTPUT_FILE_NAME_TEMPLATE.format(index=index)
            file_path.write_bytes(image.content)
            output_refs.append(
                OutputRef(
                    path=_project_relative_path(file_path),
                    mime_type=image.mime_type,
                    byte_size=len(image.content),
                )
            )
        return output_refs

    def _load_retry_prompt_artifact(
        self, session_id: str, generation_id: str | None
    ) -> PromptArtifact:
        """加载 retry 要复用的 PromptArtifact（原 Artifact 路径，缺失则 Rev.2 回退路径）。

        - `generation_id` 为 None → 显式选本会话最新已落库 Prompt；空字符串仍拒绝；
        - 原 GenerationArtifact 存在 → 取 `refs["prompt_artifact_id"]` 读回同一
          PromptArtifact（跨会话 → `generation.session_mismatch`）；
        - 原 GenerationArtifact 不存在（纯 Provider 失败不写 Artifact）→
          `get_latest_prompt_artifact(session_id)`；本会话从未编译过 Prompt（None）→
          仍抛 `generation.artifact_not_found`。

        两条路径都只读取**已落库**的 PromptArtifact，绝不重新 compile。
        """
        if generation_id is None:
            return self._latest_retry_prompt_artifact(session_id)
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise GenerationError(
                GENERATION_ARTIFACT_NOT_FOUND,
                "retry requires the generation_id of the failed generation attempt",
            )
        try:
            stored = self._repo.get_generation_artifact(generation_id)
        except RepositoryError:
            # 纯 Provider 失败按契约不写 GenerationArtifact → 回退到本会话最新
            # PromptArtifact（即该次失败 generate 刚编译落库的那一条）。查询按
            # session_id 过滤，故无需再校验跨会话。
            return self._latest_retry_prompt_artifact(session_id)
        if stored.session_id != session_id:
            raise GenerationError(
                GENERATION_SESSION_MISMATCH,
                f"generation {generation_id!r} belongs to another session",
            )
        return self._load_prompt_artifact(session_id, stored.refs["prompt_artifact_id"])

    def _latest_retry_prompt_artifact(self, session_id: str) -> PromptArtifact:
        latest = self._repo.get_latest_prompt_artifact(session_id)
        if latest is None:
            raise GenerationError(
                GENERATION_ARTIFACT_NOT_FOUND,
                f"session {session_id!r} has no persisted PromptArtifact to retry",
            ) from None
        return PromptArtifact.model_validate_json(latest.payload)

    def _load_prompt_artifact(self, session_id: str, prompt_artifact_id: str) -> PromptArtifact:
        try:
            stored = self._repo.get_prompt_artifact(prompt_artifact_id)
        except RepositoryError as exc:
            raise GenerationError(
                GENERATION_ARTIFACT_NOT_FOUND,
                f"the original generation references a missing PromptArtifact "
                f"{prompt_artifact_id!r}",
            ) from exc
        if stored.session_id != session_id:
            raise GenerationError(
                GENERATION_SESSION_MISMATCH,
                f"PromptArtifact {prompt_artifact_id!r} belongs to another session",
            )
        return PromptArtifact.model_validate_json(stored.payload)

    # -- 失败记录（结构化日志 + 状态迁移） ----------------------------------

    def _log_and_mark_failed(self, session_id: str, **fields: object) -> None:
        """结构化日志（不伪造 Artifact）并把 GENERATING 推进到 FAILED。

        日志字段只含 ID、错误分类与状态码；不含 key、Authorization 头或临时 URL。
        状态迁移是 best-effort：绝不用新的异常掩盖最初的失败原因。
        """
        payload = {"event": GENERATION_FAILED_EVENT, "session_id": session_id, **fields}
        _LOGGER.error("image generation failed", extra=payload)
        try:
            snapshot = self._repo.get_current_session_snapshot(session_id)
            if snapshot.workflow_state is WorkflowState.GENERATING:
                self._repo.transition_state(session_id, WorkflowState.FAILED)
        except Exception:  # noqa: BLE001 - 失败记录绝不再抛，避免掩盖原始异常
            _LOGGER.exception(
                "could not record the FAILED workflow state for session %r",
                session_id,
                extra={"event": GENERATION_FAILED_EVENT, "session_id": session_id},
            )


def _project_relative_path(file_path: Path) -> str:
    """把落盘路径转成**相对项目根**的稳定引用（可用 `PROJECT_ROOT / path` 还原）。

    默认 `output_dir`（`<项目根>/outputs/generations`）下即形如
    `outputs/generations/<generation_id>/image_1.png`；测试用 `tmp_path` 时得到
    `../../tmp/...` 形态的相对路径，仍满足"相对项目根 + 可重新定位"且绝不是 URL。
    """
    absolute = Path(file_path).resolve()
    root = Path(PROJECT_ROOT).resolve()
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        relative = Path(os.path.relpath(absolute, root))
    return relative.as_posix()


__all__ = ["GenerationPipeline", "GENERATION_FAILED_EVENT", "_project_relative_path"]
