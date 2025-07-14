import yaml

class Config:
    def __init__(self, path: str):
        self.path = path
        self.data = self.load_config()

    def load_config(self):
        try:
            with open(self.path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            return {}

    def save_config(self):
        with open(self.path, 'w') as f:
            yaml.dump(self.data, f)
