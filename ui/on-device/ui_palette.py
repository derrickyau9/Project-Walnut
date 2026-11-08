"""Neutral application palette, including focus rings and text selections."""
from PyQt5.QtGui import QColor,QPalette

def apply_monochrome_palette(app):
    palette=QPalette()
    colors={QPalette.Window:'#111111',QPalette.WindowText:'#eeeeee',QPalette.Base:'#171717',
            QPalette.AlternateBase:'#222222',QPalette.Text:'#eeeeee',QPalette.Button:'#292929',
            QPalette.ButtonText:'#eeeeee',QPalette.Highlight:'#dddddd',QPalette.HighlightedText:'#080808',
            QPalette.Link:'#dddddd',QPalette.LinkVisited:'#aaaaaa',QPalette.BrightText:'#ffffff',
            QPalette.Light:'#555555',QPalette.Midlight:'#444444',QPalette.Mid:'#555555',QPalette.Dark:'#080808',
            QPalette.Shadow:'#000000',QPalette.ToolTipBase:'#222222',QPalette.ToolTipText:'#eeeeee'}
    for group in (QPalette.Active,QPalette.Inactive,QPalette.Disabled):
        for role,value in colors.items():palette.setColor(group,role,QColor(value))
    for role in (QPalette.WindowText,QPalette.Text,QPalette.ButtonText):palette.setColor(QPalette.Disabled,role,QColor('#777777'))
    app.setPalette(palette)
