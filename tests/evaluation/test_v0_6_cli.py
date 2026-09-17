"""Step 02：CLI 离线入口（list-cases 与 real 拒绝；零网络）。"""

from __future__ import annotations

from evaluation.v0_6.paired_runner import main


def test_cli_list_cases_prints_the_frozen_30_contexts(capsys):
    assert main(["--list-cases"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 30
    assert all(line.count("\t") == 3 for line in lines)


def test_cli_real_provider_is_refused_and_never_constructs_one(capsys):
    # 无 G1 授权 + dirty 身份 + budget/threshold 缺失 → fail-closed，退出码 3。
    assert main(["--provider", "real"]) == 3
    err = capsys.readouterr().err
    assert "refused" in err
