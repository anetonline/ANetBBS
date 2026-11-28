"""Template and client-side regression checks for file description display."""
from pathlib import Path
import shutil
import subprocess

import pytest
from jinja2 import Environment, DictLoader

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'anetbbs/templates/file_areas/area.html'
SCRIPT = ROOT / 'anetbbs/static/js/file-description-view.js'


def test_description_controls_do_not_target_share_dialogs():
    env = Environment(loader=DictLoader({
        'area.html': TEMPLATE.read_text(),
        'base.html': '{% block title %}{% endblock %}{% block content %}{% endblock %}',
    }), autoescape=True)
    env.filters.update(eastern=lambda value, fmt: value, strip_ansi=str, ansi_art=str)
    html = env.get_template('area.html').render(
        area=dict(id=1, tag='TEST', upload_permission='sysop'),
        current_user=dict(is_admin=False), sort='name',
        files=[dict(name='LONG.ZIP', size=1024, mtime='2026-01-01', description='First\nSecond'),
               dict(name='SHORT.ZIP', size=1024, mtime='2026-01-01', description='Single line')],
        t=lambda key, fallback: fallback, csrf_token=lambda: 'token',
        url_for=lambda endpoint, **kwargs: '/' + endpoint)
    assert html.count('class="mt-1 file-description-details"') == 1
    assert html.count('data-description-mode=') == 2
    assert html.count('class="mt-1">') == 2  # independent share dialogs
    assert 'Single line' in html and 'First\nSecond' in html
    assert 'id="file-description-controls" class="d-none' in html


@pytest.mark.skipif(not shutil.which('node'), reason='Node.js not installed')
def test_description_toggle_and_storage():
    harness = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
let saved = null;
function element(classes = []) {
    const set = new Set(classes), events = {};
    return {open: false, classList: {
        contains: k => set.has(k), remove: k => set.delete(k),
        toggle: (k, on) => on ? set.add(k) : set.delete(k)
    }, addEventListener: (k, fn) => events[k] = fn,
    emit: k => events[k](), setAttribute(k,v) {this[k]=v;}};
}
function page(blocked = false) {
    const detail = element(), other = element(), share = element();
    detail.previousElementSibling = element(['file-description-preview']);
    other.previousElementSibling = element(['file-description-preview']);
    const compact = element(), full = element(), controls = element(['d-none']);
    compact.dataset = {descriptionMode:'compact'};
    full.dataset = {descriptionMode:'full'};
    controls.querySelectorAll = () => [compact, full];
    vm.runInNewContext(source, {
        document: {getElementById: () => controls, querySelectorAll: selector => {
            assert.equal(selector, 'details.file-description-details');
            return [detail, other];
        }}, localStorage: {
            getItem: () => {if (blocked) throw Error(); return saved;},
            setItem: (key, value) => {if (blocked) throw Error(); saved = value;}
        }
    });
    return {detail, other, share, compact, full, controls};
}
let p = page();
assert.equal(p.detail.open, false);
assert.equal(p.controls.classList.contains('d-none'), false);
p.full.emit('click');
assert.equal(p.detail.open, true);
assert.equal(p.other.open, true);
assert.equal(p.share.open, false);
assert.equal(p.detail.previousElementSibling.classList.contains('d-none'), true);
assert.equal(p.full['aria-pressed'], 'true');
assert.equal(saved, 'full');
p = page();
assert.equal(p.detail.open, true);
p.detail.open = false;
p.detail.emit('toggle');
assert.equal(p.detail.previousElementSibling.classList.contains('d-none'), false);
p.compact.emit('click');
assert.equal(p.other.open, false);
assert.equal(p.compact['aria-pressed'], 'true');
assert.equal(saved, 'compact');
p = page(true);
p.full.emit('click');
assert.equal(p.detail.open, true);
'''
    subprocess.run(['node', '-e', harness, str(SCRIPT)], check=True)
