import yaml


def parse(text):
    return yaml.unsafe_load(text)
