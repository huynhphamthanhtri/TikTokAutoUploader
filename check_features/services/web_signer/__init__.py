# -*- coding: utf-8 -*-
"""
Package web_signer: Cung cấp WebSignerAdapter cho Creator Studio và Web endpoints.
"""

from .adapter import WebSignerAdapter, DEFAULT_WEB_UA
from .bogus import Signer as BogusSigner
from .gnarly import get_X_Gnarly

__all__ = ["WebSignerAdapter", "DEFAULT_WEB_UA", "BogusSigner", "get_X_Gnarly"]
