from pygments.styles import get_style_by_name
from pygments.styles.friendly import FriendlyStyle
from pygments.token import Token


class SoftStyle(FriendlyStyle):
    background_color = '#dde8de'
    styles = dict(FriendlyStyle.styles)
    styles.update({token: '#49695c italic' for token in FriendlyStyle.styles if token in Token.Comment})
    styles.update({token: '#315e51' for token in FriendlyStyle.styles if token in Token.Literal.Number})
    styles.update({Token.Comment: '#49695c italic', Token.Name.Attribute: '#285d83',
                   Token.Name.Class: '#185b7d bold', Token.Name.Namespace: '#185b7d bold',
                   Token.Name.Variable: '#6c3d70', Token.Literal.String: '#285d83',
                   Token.Literal.String.Doc: '#49695c italic', Token.Generic.Emph: '#285d83 italic'})


def style_for_theme(theme):
    return SoftStyle if theme == 'soft' else get_style_by_name('github-dark' if theme in ('dark', 'nv') else 'friendly')
