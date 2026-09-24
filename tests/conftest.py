import json
import pytest


@pytest.fixture
def fake_io(monkeypatch, tmp_path):
    """
    Fixture para mockar load_json_data/save_json_data em módulos que fazem I/O.
    Usa dicionário em memória para armazenar conteúdos.
    """
    storage = {}

    def _fake_load(path: str):
        return storage.get(path)

    def _fake_save(data, path: str, timestamp: bool = True):
        storage[path] = data
        return path
    
    def _fake_scan_directory(directory: str):
        """Mock scan_data_directory to return files from fake_io"""
        return [k for k in storage.keys() if k.startswith(directory)]
    
    def _fake_path_exists(path: str):
        """Mock os.path.exists to check fake_io storage"""
        return path in storage
    
    def _fake_getsize(path: str):
        """Mock os.path.getsize to return fake size"""
        if path in storage:
            data = storage[path]
            return len(json.dumps(data)) if data else 0
        return 0
    
    def _fake_getmtime(path: str):
        """Mock os.path.getmtime to return current timestamp"""
        import time
        return time.time()

    # Patch in utils.github_api module
    monkeypatch.setattr("coops.utils.github_api.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.utils.github_api.save_json_data", _fake_save, raising=False)
    
    # Patch in registry_manager module
    monkeypatch.setattr("coops.etl.registry_manager.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.etl.registry_manager.save_json_data", _fake_save, raising=False)
    
    # Also patch in silver modules (they import directly)
    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", _fake_save, raising=False)
    monkeypatch.setattr("coops.silver.contribution_metrics.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.silver.contribution_metrics.save_json_data", _fake_save, raising=False)
    monkeypatch.setattr("coops.silver.collaboration_networks.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.silver.collaboration_networks.save_json_data", _fake_save, raising=False)
    monkeypatch.setattr("coops.silver.temporal_analysis.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.silver.temporal_analysis.save_json_data", _fake_save, raising=False)
    
    # Also patch in gold modules
    monkeypatch.setattr("coops.gold.timeline_aggregation.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.gold.timeline_aggregation.save_json_data", _fake_save, raising=False)

    # Patch members_statistics module
    monkeypatch.setattr("coops.silver.members_statistics.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.silver.members_statistics.save_json_data", _fake_save, raising=False)

    # Silver reads Bronze through the shared per-repository loader (#170).
    # The storage keeps the "<family>_all.json" keys the tests populate: the
    # key names the family's records, it is not a path the code reads.
    def _fake_load_family(family: str):
        return storage.get(f"data/bronze/{family}_all.json") or []

    monkeypatch.setattr("coops.silver.members_statistics.load_family", _fake_load_family, raising=False)
    monkeypatch.setattr("coops.silver.contribution_metrics.load_family", _fake_load_family, raising=False)
    monkeypatch.setattr("coops.silver.collaboration_networks.load_family", _fake_load_family, raising=False)
    monkeypatch.setattr("coops.silver.temporal_analysis.load_family", _fake_load_family, raising=False)

    # The Bronze writers remove the retired "<family>_all.json" aggregates
    # where they used to write them (#170). Route that through the fake too,
    # so a test exercising a writer stays inside the in-memory storage.
    def _fake_remove_aggregate(bronze_dir, family):
        return storage.pop(f"{bronze_dir}/{family}_all.json", None)

    monkeypatch.setattr("coops.bronze.issues.remove_aggregate", _fake_remove_aggregate, raising=False)
    monkeypatch.setattr("coops.bronze.commits.remove_aggregate", _fake_remove_aggregate, raising=False)

    # Patch file_language_analysis module
    monkeypatch.setattr("coops.silver.file_language_analysis.load_json_data", _fake_load, raising=False)
    monkeypatch.setattr("coops.silver.file_language_analysis.save_json_data", _fake_save, raising=False)
    
    # Patch registry_manager functions
    monkeypatch.setattr("coops.etl.registry_manager.scan_data_directory", _fake_scan_directory, raising=False)
    
    # Patch os.path functions in registry_manager
    import os
    original_exists = os.path.exists
    original_getsize = os.path.getsize
    original_getmtime = os.path.getmtime
    
    def _conditional_exists(path):
        if path in storage:
            return True
        return original_exists(path)
    
    def _conditional_getsize(path):
        if path in storage:
            return _fake_getsize(path)
        return original_getsize(path)
    
    def _conditional_getmtime(path):
        if path in storage:
            return _fake_getmtime(path)
        return original_getmtime(path)
    
    monkeypatch.setattr("os.path.exists", _conditional_exists)
    monkeypatch.setattr("os.path.getsize", _conditional_getsize)
    monkeypatch.setattr("os.path.getmtime", _conditional_getmtime)
    
    return storage