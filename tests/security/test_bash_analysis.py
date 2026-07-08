"""bash quote-aware 分段与风险分级(迭代 3「深防御」)。"""

from __future__ import annotations

from tianshu.security.bash_analysis import (
    analyze_command,
    has_unquoted_command_substitution,
    has_unquoted_redirection,
    has_unquoted_single_ampersand,
    split_unquoted_segments,
)


class TestSplitSegments:
    def test_semicolon(self):
        assert split_unquoted_segments("git log; rm -rf /") == ["git log", "rm -rf /"]

    def test_pipe_and_logical(self):
        assert split_unquoted_segments("a | b") == ["a", "b"]
        assert split_unquoted_segments("a && b || c") == ["a", "b", "c"]

    def test_quoted_separators_preserved(self):
        assert split_unquoted_segments("echo 'a; b'") == ["echo 'a; b'"]
        assert split_unquoted_segments('echo "x | y"') == ['echo "x | y"']

    def test_newline_splits(self):
        assert split_unquoted_segments("a\nb") == ["a", "b"]

    def test_empty_segments_dropped(self):
        assert split_unquoted_segments(";; ; ") == []


class TestStructuralRisk:
    def test_command_substitution(self):
        assert has_unquoted_command_substitution("cat $(whoami)")
        assert has_unquoted_command_substitution("echo `id`")
        assert not has_unquoted_command_substitution("echo '$(safe)'")

    def test_redirection(self):
        assert has_unquoted_redirection("echo x > /etc/passwd")
        assert not has_unquoted_redirection("echo '> not redirect'")

    def test_single_ampersand_background(self):
        assert has_unquoted_single_ampersand("sleep 100 &")
        assert not has_unquoted_single_ampersand("a && b")
        assert not has_unquoted_single_ampersand("echo '&'")

    def test_analysis_aggregate(self):
        a = analyze_command("git log; curl x > /tmp/out & echo $(id)")
        assert a.has_background and a.has_redirection and a.has_substitution
        assert a.has_structural_risk
        assert len(a.structural_notes) == 3
