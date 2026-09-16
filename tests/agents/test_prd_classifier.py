# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

from unittest.mock import patch

import pytest

with patch("pydantic_ai.mcp.MCPServerSSE"):
    from agents.test_case_generation.main import (
        _chunk_by_sections,
        _classify_prd,
        _merge_short_sections,
        _parse_sections_from_html,
        _parse_sections_from_text,
        _Section,
        _split_long_sections,
    )


# ---------------------------------------------------------------------------
# _classify_prd
# ---------------------------------------------------------------------------


class TestClassifyPrd:
    def test_explicit_ac_list_chinese(self):
        assert _classify_prd("## 验收标准\n1. 系统应...") == "ac_list"

    def test_explicit_ac_list_english(self):
        assert _classify_prd("## Acceptance Criteria\n1. System shall...") == "ac_list"

    def test_functional_numbered_sections(self):
        content = "\n".join(f"{i}.{j} 小节标题" for i in range(1, 4) for j in range(1, 4))
        assert _classify_prd(content) == "functional"

    def test_functional_threshold_exact(self):
        # Exactly at threshold (default 5)
        content = " ".join(f"{i}.{j}" for i in range(1, 3) for j in range(1, 4))  # 6 matches
        with patch("agents.test_case_generation.main.config") as mock_cfg:
            mock_cfg.PrdClassifierConfig.FUNCTIONAL_SECTION_THRESHOLD = 5
            assert _classify_prd(content) == "functional"

    def test_functional_below_threshold_is_narrative(self):
        content = "1.1 节一 1.2 节二"  # only 2 section numbers
        with patch("agents.test_case_generation.main.config") as mock_cfg:
            mock_cfg.PrdClassifierConfig.FUNCTIONAL_SECTION_THRESHOLD = 5
            assert _classify_prd(content) == "narrative"

    def test_narrative_plain_text(self):
        assert _classify_prd("这是一段没有结构的需求描述文字。") == "narrative"

    def test_ac_takes_priority_over_sections(self):
        content = "验收标准\n5.1 节一 5.2 节二 5.3 节三 5.4 节四 5.5 节五 5.6 节六"
        assert _classify_prd(content) == "ac_list"


# ---------------------------------------------------------------------------
# _parse_sections_from_html
# ---------------------------------------------------------------------------


class TestParseSectionsFromHtml:
    def test_basic_h2_parsing(self):
        html = '<h2 seq="5.1">5.1 背景</h2><p>背景内容</p><h2 seq="5.2">5.2 目标</h2><p>目标内容</p>'
        sections = _parse_sections_from_html(html)
        assert len(sections) == 2
        assert sections[0].section_id == "5.1"
        assert "背景内容" in sections[0].content

    def test_h3_parsing(self):
        html = "<h3>5.1.1 子节</h3><p>子节内容</p>"
        sections = _parse_sections_from_html(html)
        assert len(sections) == 1
        assert sections[0].section_id == "5.1.1"

    def test_no_headings_returns_empty(self):
        assert _parse_sections_from_html("<p>plain text</p>") == []

    def test_section_id_from_title_number(self):
        html = "<h2>3.2 用户故事</h2><p>内容</p>"
        sections = _parse_sections_from_html(html)
        assert sections[0].section_id == "3.2"

    def test_section_without_leading_number_gets_fallback_id(self):
        html = "<h2>简介</h2><p>内容</p>"
        sections = _parse_sections_from_html(html)
        assert sections[0].section_id == "sec-1"


# ---------------------------------------------------------------------------
# _parse_sections_from_text
# ---------------------------------------------------------------------------


class TestParseSectionsFromText:
    def test_numbered_sections(self):
        text = "5.1 节一\n内容一\n5.2 节二\n内容二"
        sections = _parse_sections_from_text(text)
        assert len(sections) == 2
        assert sections[0].section_id == "5.1"
        assert "内容一" in sections[0].content

    def test_no_numbered_sections(self):
        assert _parse_sections_from_text("plain content") == []


# ---------------------------------------------------------------------------
# _merge_short_sections
# ---------------------------------------------------------------------------


class TestMergeShortSections:
    def test_short_section_merged_into_previous(self):
        sections = [
            _Section("5.1", "节一", "a" * 500),
            _Section("5.2", "节二", "short"),  # < 300 chars
        ]
        result = _merge_short_sections(sections, min_chars=300)
        assert len(result) == 1
        assert "short" in result[0].content

    def test_first_section_never_merged_away(self):
        sections = [_Section("5.1", "节一", "short")]
        result = _merge_short_sections(sections, min_chars=300)
        assert len(result) == 1

    def test_long_section_kept(self):
        sections = [
            _Section("5.1", "节一", "a" * 500),
            _Section("5.2", "节二", "b" * 500),
        ]
        result = _merge_short_sections(sections, min_chars=300)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _split_long_sections
# ---------------------------------------------------------------------------


class TestSplitLongSections:
    def test_long_section_split_on_sub_headings(self):
        body = "前言内容\n5.1.1 子节一\n" + "x" * 500 + "\n5.1.2 子节二\n" + "y" * 500
        sections = [_Section("5.1", "节一", body)]
        result = _split_long_sections(sections, max_chars=200)
        ids = [s.section_id for s in result]
        assert "5.1.1" in ids
        assert "5.1.2" in ids

    def test_short_section_not_split(self):
        sections = [_Section("5.1", "节一", "a" * 100)]
        result = _split_long_sections(sections, max_chars=200)
        assert len(result) == 1

    def test_long_section_without_sub_headings_kept_as_is(self):
        sections = [_Section("5.1", "节一", "x" * 5000)]
        result = _split_long_sections(sections, max_chars=200)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _chunk_by_sections (integration)
# ---------------------------------------------------------------------------


class TestChunkBySections:
    def test_produces_non_empty_chunks_from_html(self):
        html = (
            "<h2>5.1 功能概述</h2><p>" + "内容 " * 60 + "</p>"
            "<h2>5.2 详细设计</h2><p>" + "详情 " * 60 + "</p>"
        )
        with patch("agents.test_case_generation.main.config") as mock_cfg:
            mock_cfg.PrdClassifierConfig.SECTION_MIN_CHARS = 50
            mock_cfg.PrdClassifierConfig.SECTION_MAX_CHARS = 5000
            result = _chunk_by_sections(html)
        assert len(result.items) >= 1
        assert all(item.source_type == "section" for item in result.items)

    def test_chunk_ids_match_section_numbers(self):
        html = "<h2>3.1 背景</h2><p>" + "x" * 100 + "</p>"
        with patch("agents.test_case_generation.main.config") as mock_cfg:
            mock_cfg.PrdClassifierConfig.SECTION_MIN_CHARS = 10
            mock_cfg.PrdClassifierConfig.SECTION_MAX_CHARS = 5000
            result = _chunk_by_sections(html)
        assert result.items[0].id == "3.1"

    def test_empty_sections_excluded(self):
        html = "<h2>5.1 空节</h2><p>   </p><h2>5.2 实节</h2><p>" + "内容 " * 50 + "</p>"
        with patch("agents.test_case_generation.main.config") as mock_cfg:
            mock_cfg.PrdClassifierConfig.SECTION_MIN_CHARS = 10
            mock_cfg.PrdClassifierConfig.SECTION_MAX_CHARS = 5000
            result = _chunk_by_sections(html)
        ids = [item.id for item in result.items]
        assert "5.2" in ids

    def test_fallback_to_text_parsing_when_no_html_headings(self):
        text = "5.1 节一\n" + "内容 " * 60 + "\n5.2 节二\n" + "内容 " * 60
        with patch("agents.test_case_generation.main.config") as mock_cfg:
            mock_cfg.PrdClassifierConfig.SECTION_MIN_CHARS = 50
            mock_cfg.PrdClassifierConfig.SECTION_MAX_CHARS = 5000
            result = _chunk_by_sections(text)
        assert len(result.items) >= 1
