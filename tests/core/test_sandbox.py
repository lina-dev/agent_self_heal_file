import pytest

from audio_repair.core.sandbox import JobSandbox, run_argv


def test_sandbox_creates_and_cleans(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        p = sb.resolve("out.wav")
        p.write_text("x")
        assert p.exists()
        saved = sb.path
    assert not saved.exists()


def test_sandbox_blocks_path_escape(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        with pytest.raises(ValueError):
            sb.resolve("../escape.txt")
        with pytest.raises(ValueError):
            sb.resolve("/etc/passwd")
        with pytest.raises(ValueError):
            sb.resolve("a/../../b")


def test_sandbox_allows_nested(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        p = sb.resolve("sub/dir/out.wav")
        assert str(p).startswith(str(sb.path.resolve()))


def test_run_argv_rejects_string():
    with pytest.raises(ValueError):
        run_argv("echo hi", timeout_s=5)  # type: ignore[arg-type]


def test_run_argv_rejects_empty():
    with pytest.raises(ValueError):
        run_argv([], timeout_s=5)


def test_run_argv_basic():
    r = run_argv(["printf", "hello"], timeout_s=5)
    assert r.returncode == 0
    assert r.stdout == "hello"
    assert r.timed_out is False


def test_run_argv_missing_executable():
    r = run_argv(["this_command_does_not_exist_xyz"], timeout_s=5)
    assert r.returncode == 127
    assert r.timed_out is False


def test_run_argv_timeout():
    r = run_argv(["sleep", "5"], timeout_s=1)
    assert r.timed_out is True
    assert r.returncode != 0
