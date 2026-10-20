import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import wx
    wx.DisableAsserts()
except ImportError:
    pass
