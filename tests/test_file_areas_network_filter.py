"""Feature test: Admin -> File Areas had no way to narrow the list down
to just one network (or just local areas) -- a real usability
complaint once a sysop has enough areas to make hunting through one
long flat list tedious ("would be much easier when you have a lot of
file areas and want to only work on a certain group").

Client-side only (no new route, no server round-trip): a "Show:"
filter <select> hides/shows each area's per-area <form> by comparing
its data-network-id against the selected value, reusing the exact
same data-network-id values the existing bulk-select-network dropdown
already relies on (see test_file_areas_bulk_actions.py). This test
can only verify the rendered markup carries what the JS needs to work
correctly -- it can't execute filterFileAreas() itself without a real
browser -- so it checks the filter <select>'s options, each area's
data-network-id attribute, and that "Select all shown" only selects
currently-visible checkboxes (documented via the onchange handler's
own visibility check, not independently re-verifiable here either).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FileAreaNetworkFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_areas_filter_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, EchomailNetwork, FileArea
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

            admin = User(username='fileareafiltertest', is_admin=True,
                        access_level=255,
                        email='fileareafiltertest@example.com')
            admin.set_password('x')
            db.session.add(admin)

            net = EchomailNetwork(name='FilterTestNet', network_type='binkp',
                                  our_address='9:9/1')
            db.session.add(net)
            db.session.commit()
            cls.net_id = net.id

            local_area = FileArea(tag='FILTERLOCAL', name='Local test area',
                                  network_id=None, is_active=True)
            net_area = FileArea(tag='FILTERNET', name='Network test area',
                                network_id=net.id, is_active=True)
            db.session.add_all([local_area, net_area])
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_page_renders_with_filter_dropdown_and_network_options(self):
        client = self._admin_client()
        resp = client.get('/admin/file-areas')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('id="areaFilterNetwork"', body)
        self.assertIn('Local only (no network)', body)
        self.assertIn('FilterTestNet only', body)

    def test_each_area_form_carries_its_network_id_for_the_filter_js(self):
        client = self._admin_client()
        body = client.get('/admin/file-areas').get_data(as_text=True)
        self.assertIn(f'data-network-id="{self.net_id}"', body,
                      'network-linked area must expose its network id for filterFileAreas()')
        self.assertIn('data-network-id="none"', body,
                      'local area (network_id=NULL) must use the "none" sentinel, '
                      'matching the existing bulk-select-network dropdown convention')

    def test_filter_function_and_select_all_shown_wiring_present(self):
        client = self._admin_client()
        body = client.get('/admin/file-areas').get_data(as_text=True)
        self.assertIn('function filterFileAreas', body)
        self.assertIn('Select all shown', body)


if __name__ == '__main__':
    unittest.main()
