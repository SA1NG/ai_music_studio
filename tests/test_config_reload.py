from shared import config


def test_reload_reads_edited_dotenv_and_keeps_explicit_environment(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('AUTOLABEL_BACKEND=mock\nGENERATE_BACKEND=ace\n', encoding='utf-8')
    monkeypatch.setenv('ENV_FILE', str(path))
    monkeypatch.delenv('AUTOLABEL_BACKEND', raising=False)
    monkeypatch.setenv('GENERATE_BACKEND', 'mock')
    monkeypatch.setattr(config, '_settings', None)
    monkeypatch.setattr(config, '_file_env_values', {})
    first = config.get_settings()
    assert first.autolabel_backend == 'mock'
    path.write_text('AUTOLABEL_BACKEND=ace\nGENERATE_BACKEND=ace\n', encoding='utf-8')
    second = config.reload_settings()
    assert second.autolabel_backend == 'ace'
    assert second.generate_backend == 'mock'
    assert second is not first


def test_reload_preserves_environment_edited_after_dotenv_load(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('AUTOLABEL_BACKEND=mock\n', encoding='utf-8')
    monkeypatch.setenv('ENV_FILE', str(path))
    monkeypatch.delenv('AUTOLABEL_BACKEND', raising=False)
    monkeypatch.setattr(config, '_settings', None)
    monkeypatch.setattr(config, '_file_env_values', {})
    config.get_settings()
    monkeypatch.setenv('AUTOLABEL_BACKEND', 'ace')
    assert config.reload_settings().autolabel_backend == 'ace'
