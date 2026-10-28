import os
import traceback
from pathlib import Path

try:
    from collecte_cfe.app import main

    if __name__ == '__main__':
        raise SystemExit(main())
except Exception:
    try:
        from collecte_cfe.paths import data_dir

        directory = data_dir()
    except Exception:
        directory = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Collecte CFE'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'erreur-demarrage.txt').write_text(traceback.format_exc(), encoding='utf-8')
    raise
