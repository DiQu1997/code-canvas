from cv import *


def root(base):
    base['blocks'] = [scene(400, 200, [part('a', 'A', [10, 10, 150, 120], kind='act', say='x',
        card=card('a', 'b', [note('推断', 'c')]))], [], mw=340, mh=200)]
    base['blocks'][0]['parts'][0]['m'] = [8, 8, 200, 120]
    return base
