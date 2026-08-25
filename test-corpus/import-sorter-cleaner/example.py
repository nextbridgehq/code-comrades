import os
import sys
import json
import re

from collections import OrderedDict


def load(path):
    with open(path) as f:
        return json.load(f)


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()
