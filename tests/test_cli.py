from backupproof import main
def test_cli_requires_arguments(monkeypatch):
    monkeypatch.setattr('sys.argv',['backupproof'])
    try:main()
    except SystemExit as exc:assert exc.code==2
