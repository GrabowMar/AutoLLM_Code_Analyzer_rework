import pickle

import yaml


def load(blob, text):
    return pickle.loads(blob), yaml.load(text)
