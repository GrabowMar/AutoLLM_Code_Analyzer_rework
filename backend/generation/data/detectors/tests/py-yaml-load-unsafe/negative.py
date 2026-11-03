import yaml


def parse(text):
    return yaml.safe_load(text)


def parse_strict(text):
    return yaml.load(text, Loader=yaml.SafeLoader)
