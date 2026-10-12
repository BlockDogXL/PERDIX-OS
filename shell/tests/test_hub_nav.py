"""Control Center menu: numbers come from the order in NAV, groups get headings, the menu scrolls."""

from PyQt5.QtWidgets import QApplication

from kiosk_shell import perdix_hub
from kiosk_shell.perdix_hub import NAV, HubWindow, PageHeader, module_number

app = QApplication.instance() or QApplication([])


def test_numbers_follow_the_menu_order():
    assert module_number(NAV[0][0]) == "01"
    assert [m[0] for m in HubWindow.MODULES] == [f"{i:02d}" for i in range(1, len(NAV) + 1)]
    assert len({entry[0] for entry in NAV}) == len(NAV)  # keys are unique


def test_groups_are_contiguous():
    groups = [entry[4] for entry in NAV]
    seen = []
    for group in groups:
        if not seen or seen[-1] != group:
            assert group not in seen, f"{group} appears in two places"
            seen.append(group)


def test_every_page_shows_its_own_number():
    window = HubWindow()
    for i, (key, *_rest) in enumerate(NAV):
        page = getattr(window, key)
        header = page.findChild(PageHeader)
        eyebrow = header.findChildren(perdix_hub.QLabel)[0].text()
        assert eyebrow.startswith(f"MODULE {i + 1:02d}") or eyebrow.startswith(f"MODÜL {i + 1:02d}"), (key, eyebrow)
        assert window.pages.widget(i).widget().findChild(type(page)) is page
    window.close()


def test_menu_scrolls_to_the_chosen_entry():
    window = HubWindow()
    window.resize(window.minimumSize())
    window.show()
    app.processEvents()
    assert [h.text() for h in window.sidebar.headings] == [g.upper() for g in dict.fromkeys(e[4] for e in NAV)]
    last = window.sidebar.buttons[-1]
    last.click()
    app.processEvents()
    viewport = window.sidebar.scroll.viewport()
    top = last.mapTo(viewport, last.rect().topLeft()).y()
    assert 0 <= top and top + last.height() <= viewport.height()  # visible after the click
    assert window.pages.currentIndex() == len(NAV) - 1
    window.close()
