from dataclasses import dataclass
import json
import pickle

@dataclass
class Utilities:
    @staticmethod
    def load_config_from_file(path: str):
        with open(path, "r") as f:
            return json.load(f)

    @staticmethod
    def load_from_file_with_pickle(filename: str):
        with open(filename, 'rb') as f:
            return pickle.load(f)

    @staticmethod
    def dump_to_file_with_pickle(metadata: any, file: str):
        with open(file, "wb") as f:
            pickle.dump(metadata, f)
