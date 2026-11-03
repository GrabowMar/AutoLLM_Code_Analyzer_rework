import json

import yaml


def load(blob, text):
    return json.loads(blob), yaml.load(text, Loader=yaml.SafeLoader)
