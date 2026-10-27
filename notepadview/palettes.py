"""Shared colors for Qt controls, Scintilla and HTML previews."""

THEMES = {
    'nv': dict(bg='#122035', pane='#14243d', fg='#cdd2d7', muted='#9cadb2',
               border='#29405f', hover='#203b59', accent='#8baed6',
               selection='#343c3e', code='#182a46', header='#15253e',
               preview_frame='#1c3253', paper='#e7edf4', paper_fg='#1b3152',
               navy='#003080', cyan='#89acb7', violet='#8baed6'),
    'light': dict(bg='#ffffff', pane='#f5f6f8', fg='#282c32', muted='#697583',
                  border='#dfe4e8', hover='#e8edf0', accent='#087f68',
                  selection='#c9e9df', code='#f4f6f8'),
    'dark': dict(bg='#202124', pane='#292b30', fg='#e5e7eb', muted='#a8adb7',
                 border='#3e4249', hover='#383c43', accent='#79d6bd',
                 selection='#36524d', code='#292b30'),
    'soft': dict(bg='#e8f1e8', pane='#dce8dc', fg='#283c32', muted='#607566',
                 border='#bdcebf', hover='#cfdfd0', accent='#30664b',
                 selection='#bad7c0', code='#dde8de'),
}


def theme_colors(theme):
    return THEMES.get(theme, THEMES['light'])
