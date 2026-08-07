"""Graffiti Wall section (anetbbs-cfg) -- post moderation (soft-delete/
restore/clear-all). InterBBS Wall sharing settings (color scheme, which
BinkP network relays it, auto-creating the ANET_WALL echo area) involve
writing .env keys AND provisioning a real echomail area together in one
web-admin route -- left web-admin-only for now (Admin -> Wall) rather
than partially reimplementing that multi-step flow.
"""
from anetbbs.cfg import ui
from anetbbs.models import db, WallPost

COLUMNS = [
    ("ID", 6, lambda p: p.id),
    ("User", 16, lambda p: p.username),
    ("Line 1", 26, lambda p: (p.line1 or "")[:26]),
    ("Posted", 17, lambda p: p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else ""),
    ("Origin", 12, lambda p: p.origin_bbs or "(local)"),
]


def list_active_posts(limit=200):
    return (WallPost.query.filter_by(is_deleted=False)
            .order_by(WallPost.created_at.desc()).limit(limit).all())


def list_deleted_posts(limit=200):
    return (WallPost.query.filter_by(is_deleted=True)
            .order_by(WallPost.created_at.desc()).limit(limit).all())


def delete_post(post):
    post.is_deleted = True
    db.session.commit()


def restore_post(post):
    post.is_deleted = False
    db.session.commit()


def clear_all_posts():
    count = WallPost.query.filter_by(is_deleted=False).update({"is_deleted": True})
    db.session.commit()
    return count


def _delete(stdscr, p):
    if ui.confirm(stdscr, f"Delete wall post #{p.id} by {p.username}?"):
        delete_post(p)


def _restore(stdscr, p):
    if ui.confirm(stdscr, f"Restore wall post #{p.id} by {p.username}?"):
        restore_post(p)


def _clear_all(stdscr, _row):
    if ui.confirm(stdscr, "Delete ALL active wall posts?"):
        count = clear_all_posts()
        ui.show_message(stdscr, f"Cleared {count} wall post(s).")


def _run_active(stdscr):
    ui.run_list(
        stdscr, "Graffiti Wall (active posts)", COLUMNS, list_active_posts,
        on_delete=_delete,
        extra_actions={"c": ("ClearAll", _clear_all)},
        empty_hint="(no wall posts)",
    )


def _run_deleted(stdscr):
    ui.run_list(
        stdscr, "Graffiti Wall (deleted posts)", COLUMNS, list_deleted_posts,
        extra_actions={"r": ("Restore", _restore)},
        empty_hint="(no deleted posts)",
    )


def run(stdscr):
    items = [("active", "Active Posts"), ("deleted", "Deleted Posts")]
    while True:
        choice = ui.run_menu(stdscr, "Graffiti Wall", items)
        if choice is None:
            return
        if choice == "active":
            _run_active(stdscr)
        elif choice == "deleted":
            _run_deleted(stdscr)
