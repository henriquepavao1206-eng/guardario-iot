import sys
from pathlib import Path

# Permite "import regua", "import telemetria" etc. a partir da pasta ponte/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
