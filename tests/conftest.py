"""Add project root to sys.path so tests can import src.*"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
