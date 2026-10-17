import os as _os
# IBIGS: tests never ask prstatus.ibigs.us for Forge holds or send it reports
_os.environ.setdefault("ONI_FORGE_HOLDS_URL", "")
_os.environ.setdefault("ONI_FORGE_REPORT_URL", "")
