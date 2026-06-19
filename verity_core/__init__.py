"""verity_core — shared Core for Verity and PhishFinder.

Submodules are imported explicitly by callers (e.g. ``from verity_core import
scraping``) rather than eagerly re-exported here, so that importing the package
never forces the optional ``nodriver`` dependency.
"""

__version__ = "0.2.0"
