"""Tests for coops/silver/file_language_analysis.py."""


import coops.silver.file_language_analysis as fla


# ===================================================================
# detect_language_by_extension
# ===================================================================

class TestDetectLanguageByExtension:
    def test_python(self):
        assert fla.detect_language_by_extension(".py") == "Python"

    def test_javascript(self):
        assert fla.detect_language_by_extension(".js") == "JavaScript"

    def test_typescript(self):
        assert fla.detect_language_by_extension(".ts") == "TypeScript"

    def test_tsx_is_typescript(self):
        assert fla.detect_language_by_extension(".tsx") == "TypeScript"

    def test_jsx_is_javascript(self):
        assert fla.detect_language_by_extension(".jsx") == "JavaScript"

    def test_go(self):
        assert fla.detect_language_by_extension(".go") == "Go"

    def test_rust(self):
        assert fla.detect_language_by_extension(".rs") == "Rust"

    def test_java(self):
        assert fla.detect_language_by_extension(".java") == "Java"

    def test_unknown_extension(self):
        assert fla.detect_language_by_extension(".xyz123") == "Unknown"

    def test_no_extension(self):
        assert fla.detect_language_by_extension("") == "No Extension"

    def test_case_insensitive(self):
        assert fla.detect_language_by_extension(".PY") == "Python"
        assert fla.detect_language_by_extension(".Js") == "JavaScript"

    def test_markdown(self):
        assert fla.detect_language_by_extension(".md") == "Markdown"

    def test_yaml_variants(self):
        assert fla.detect_language_by_extension(".yaml") == "YAML"
        assert fla.detect_language_by_extension(".yml") == "YAML"


# ===================================================================
# convert_tree_to_hierarchy
# ===================================================================

class TestConvertTreeToHierarchy:
    def test_empty_tree(self):
        result = fla.convert_tree_to_hierarchy([])
        assert result["name"] == "root"
        assert result["type"] == "directory"
        assert result["children"] == []

    def test_single_file(self):
        tree = [{"name": "main.py", "type": "file", "path": "main.py",
                 "object": {"byteSize": 100}}]
        result = fla.convert_tree_to_hierarchy(tree)
        assert len(result["children"]) == 1
        child = result["children"][0]
        assert child["name"] == "main.py"
        assert child["type"] == "file"
        assert child["language"] == "Python"
        assert child["size"] == 100
        assert child["extension"] == ".py"

    def test_nested_directory(self):
        tree = [
            {
                "name": "src",
                "type": "directory",
                "path": "src",
                "children": [
                    {"name": "app.ts", "type": "file", "path": "src/app.ts",
                     "object": {"byteSize": 50}},
                ]
            }
        ]
        result = fla.convert_tree_to_hierarchy(tree)
        assert len(result["children"]) == 1
        src = result["children"][0]
        assert src["type"] == "directory"
        assert len(src["children"]) == 1
        assert src["children"][0]["language"] == "TypeScript"

    def test_blob_and_tree_types(self):
        """GraphQL returns 'blob' for files and 'tree' for directories."""
        tree = [
            {
                "name": "lib",
                "type": "tree",
                "path": "lib",
                "children": [
                    {"name": "util.go", "type": "blob", "path": "lib/util.go",
                     "object": {"byteSize": 200}},
                ]
            }
        ]
        result = fla.convert_tree_to_hierarchy(tree)
        lib = result["children"][0]
        assert lib["type"] == "directory"
        assert lib["children"][0]["type"] == "file"
        assert lib["children"][0]["language"] == "Go"

    def test_missing_type_returns_none(self):
        """Nodes with unknown type are filtered out."""
        tree = [{"name": "mystery", "path": "mystery"}]
        result = fla.convert_tree_to_hierarchy(tree)
        assert result["children"] == []

    def test_directory_with_object_entries(self):
        """Directory children may come from object.entries (GraphQL)."""
        tree = [
            {
                "name": "pkg",
                "type": "tree",
                "path": "pkg",
                "object": {
                    "entries": [
                        {"name": "mod.rs", "type": "blob", "path": "pkg/mod.rs",
                         "object": {"byteSize": 80}},
                    ]
                }
            }
        ]
        result = fla.convert_tree_to_hierarchy(tree)
        assert len(result["children"][0]["children"]) == 1
        assert result["children"][0]["children"][0]["language"] == "Rust"

    def test_file_without_dot_in_name(self):
        """Files without a dot should have empty extension."""
        tree = [{"name": "Makefile", "type": "file", "path": "Makefile",
                 "object": {"byteSize": 300}}]
        result = fla.convert_tree_to_hierarchy(tree)
        child = result["children"][0]
        assert child["extension"] == ""
        assert child["language"] == "No Extension"

    def test_size_from_node_size_field(self):
        """Size falls back to node['size'] when object.byteSize missing."""
        tree = [{"name": "a.py", "type": "file", "path": "a.py", "size": 42}]
        result = fla.convert_tree_to_hierarchy(tree)
        assert result["children"][0]["size"] == 42


# ===================================================================
# calculate_language_stats
# ===================================================================

class TestCalculateLanguageStats:
    def _make_tree(self, files):
        """Build a flat tree from (name, size) tuples."""
        return [
            {"name": name, "type": "file", "path": name, "size": size}
            for name, size in files
        ]

    def test_empty_tree(self):
        result = fla.calculate_language_stats([])
        assert result["total_files"] == 0
        assert result["total_bytes"] == 0
        assert result["languages"] == []

    def test_single_language(self):
        tree = self._make_tree([("a.py", 100), ("b.py", 200)])
        result = fla.calculate_language_stats(tree)
        assert result["total_files"] == 2
        assert result["total_bytes"] == 300
        assert len(result["languages"]) == 1
        assert result["languages"][0]["language"] == "Python"
        assert result["languages"][0]["percentage"] == 100.0

    def test_multiple_languages_percentage(self):
        tree = self._make_tree([("a.py", 300), ("b.js", 100)])
        result = fla.calculate_language_stats(tree)
        assert result["total_bytes"] == 400
        langs = {l["language"]: l for l in result["languages"]}
        assert langs["Python"]["percentage"] == 75.0
        assert langs["JavaScript"]["percentage"] == 25.0

    def test_sorted_by_percentage_desc(self):
        tree = self._make_tree([("a.js", 10), ("b.py", 90)])
        result = fla.calculate_language_stats(tree)
        assert result["languages"][0]["language"] == "Python"
        assert result["languages"][1]["language"] == "JavaScript"

    def test_largest_sample_strategy(self):
        tree = self._make_tree([
            ("small.py", 10), ("big.py", 1000), ("med.py", 500)
        ])
        result = fla.calculate_language_stats(tree, max_sample_files=2, sample_strategy="largest")
        files = result["languages"][0]["files"]
        assert len(files) == 2
        assert files[0]["size"] >= files[1]["size"]

    def test_first_sample_strategy(self):
        tree = self._make_tree([
            ("a.py", 10), ("b.py", 1000), ("c.py", 500)
        ])
        result = fla.calculate_language_stats(tree, max_sample_files=2, sample_strategy="first")
        files = result["languages"][0]["files"]
        assert len(files) == 2
        assert files[0]["name"] == "a.py"
        assert files[1]["name"] == "b.py"

    def test_zero_size_files(self):
        tree = self._make_tree([("empty.py", 0), ("also.js", 0)])
        result = fla.calculate_language_stats(tree)
        assert result["total_files"] == 2
        for lang in result["languages"]:
            assert lang["percentage"] == 0

    def test_nested_directory_traversal(self):
        tree = [
            {
                "name": "src",
                "type": "directory",
                "path": "src",
                "children": [
                    {"name": "main.py", "type": "file", "path": "src/main.py", "size": 100},
                    {"name": "util.py", "type": "file", "path": "src/util.py", "size": 200},
                ]
            },
            {"name": "README.md", "type": "file", "path": "README.md", "size": 50},
        ]
        result = fla.calculate_language_stats(tree)
        assert result["total_files"] == 3
        assert result["total_bytes"] == 350

    def test_tree_type_with_object_entries(self):
        """GraphQL-style tree with object.entries."""
        tree = [
            {
                "name": "lib",
                "type": "tree",
                "path": "lib",
                "object": {
                    "entries": [
                        {"name": "a.rs", "type": "blob", "path": "lib/a.rs",
                         "object": {"byteSize": 500}},
                    ]
                }
            }
        ]
        result = fla.calculate_language_stats(tree)
        assert result["total_files"] == 1
        assert result["languages"][0]["language"] == "Rust"

    def test_extension_detection_fallback(self):
        """When node has no 'extension' key, it derives from name."""
        tree = [{"name": "app.tsx", "type": "file", "path": "app.tsx", "size": 100}]
        result = fla.calculate_language_stats(tree)
        assert result["languages"][0]["language"] == "TypeScript"


# ===================================================================
# process_file_language_analysis
# ===================================================================

class TestProcessFileLanguageAnalysis:
    def test_no_structure_files(self, monkeypatch):
        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: [])
        result = fla.process_file_language_analysis()
        assert result == []

    def test_valid_repo(self, monkeypatch):
        structure = {
            "tree": [
                {"name": "main.py", "type": "file", "path": "main.py", "size": 100},
            ],
            "owner": "org",
            "branch": "main",
            "extracted_at": "2024-01-01",
        }

        saved = {}

        def fake_load(path):
            return structure

        def fake_save(data, path, timestamp=True):
            saved[path] = data
            return path

        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: ["data/bronze/structure_myrepo.json"])
        monkeypatch.setattr(fla, "load_json_data", fake_load)
        monkeypatch.setattr(fla, "save_json_data", fake_save)

        files = fla.process_file_language_analysis()
        # Should produce: individual analysis, hierarchy, consolidated
        assert any("language_analysis_myrepo" in f for f in files)
        assert any("hierarchy_myrepo" in f for f in files)
        assert any("language_analysis_all" in f for f in files)

    def test_save_hierarchy_false(self, monkeypatch):
        structure = {
            "tree": [{"name": "a.py", "type": "file", "path": "a.py", "size": 10}],
            "owner": "org", "branch": "main", "extracted_at": "2024-01-01",
        }
        saved = {}

        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: ["data/bronze/structure_r.json"])
        monkeypatch.setattr(fla, "load_json_data", lambda p: structure)
        monkeypatch.setattr(fla, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

        files = fla.process_file_language_analysis(save_hierarchy=False)
        assert not any("hierarchy_" in f for f in files)

    def test_save_detailed_true(self, monkeypatch):
        structure = {
            "tree": [{"name": "a.py", "type": "file", "path": "a.py", "size": 10}],
            "owner": "org", "branch": "main", "extracted_at": "2024-01-01",
        }
        saved = {}

        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: ["data/bronze/structure_r.json"])
        monkeypatch.setattr(fla, "load_json_data", lambda p: structure)
        monkeypatch.setattr(fla, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

        files = fla.process_file_language_analysis(save_detailed=True)
        assert any("_detailed" in f for f in files)

    def test_invalid_structure_skipped(self, monkeypatch):
        """Repos with no 'tree' key are skipped."""
        saved = {}
        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: ["data/bronze/structure_bad.json"])
        monkeypatch.setattr(fla, "load_json_data", lambda p: {"no_tree": True})
        monkeypatch.setattr(fla, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

        files = fla.process_file_language_analysis()
        # No per-repo files, no consolidated (empty all_repo_analyses)
        assert files == []

    def test_none_structure_skipped(self, monkeypatch):
        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: ["data/bronze/structure_x.json"])
        monkeypatch.setattr(fla, "load_json_data", lambda p: None)
        monkeypatch.setattr(fla, "save_json_data", lambda d, p, **kw: p)

        files = fla.process_file_language_analysis()
        assert files == []

    def test_multiple_repos(self, monkeypatch):
        def fake_load(path):
            return {
                "tree": [{"name": "a.py", "type": "file", "path": "a.py", "size": 50}],
                "owner": "org", "branch": "main", "extracted_at": "2024-01-01",
            }

        saved = {}
        monkeypatch.setattr(fla, "bronze_files",
                            lambda directory, family: ["data/bronze/structure_r1.json",
                                                       "data/bronze/structure_r2.json"])
        monkeypatch.setattr(fla, "load_json_data", fake_load)
        monkeypatch.setattr(fla, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

        files = fla.process_file_language_analysis()
        # 2 repos × (analysis + hierarchy) + 1 consolidated = 5
        assert len(files) == 5

    def test_sample_config_in_output(self, monkeypatch):
        structure = {
            "tree": [{"name": "a.py", "type": "file", "path": "a.py", "size": 10}],
            "owner": "org", "branch": "main", "extracted_at": "2024-01-01",
        }
        saved = {}
        monkeypatch.setattr(fla, "bronze_files", lambda directory, family: ["data/bronze/structure_r.json"])
        monkeypatch.setattr(fla, "load_json_data", lambda p: structure)
        monkeypatch.setattr(fla, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

        fla.process_file_language_analysis(max_sample_files=5, sample_strategy="first")
        analysis = saved["data/silver/language_analysis_r.json"]
        assert analysis["sample_config"]["max_files_per_language"] == 5
        assert analysis["sample_config"]["strategy"] == "first"
