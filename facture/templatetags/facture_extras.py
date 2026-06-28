from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, [])


@register.filter
def logo_static_path(value):
    logo = (value or '').strip()
    if not logo:
        return 'images/logos/images.png'
    if '/' in logo:
        return logo
    return f'images/logos/{logo}'


@register.filter
def in_group(user, group_name):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return user.groups.filter(name__iexact=(group_name or '').strip()).exists()
