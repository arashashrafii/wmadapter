from pathlib import Path


class BrowserSession:
    def __init__(self, profile_path: str):
        self.profile_path = Path(profile_path)

    def ensure_profile(self):
        self.profile_path.mkdir(parents=True, exist_ok=True)
        return self.profile_path
