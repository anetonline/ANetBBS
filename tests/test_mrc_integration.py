"""
Integration tests for MRC bridge and web UI
"""
import os
import sys
import time
import subprocess
import pytest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def bridge_service():
    """Start bridge service for testing"""
    # Set up test config
    bridge_dir = Path(__file__).parent.parent / 'services' / 'mrc_bridge'
    
    # Check if bridge files exist
    if not bridge_dir.exists():
        pytest.skip("MRC bridge directory not found")
    
    # Start bridge in subprocess (using example config for testing)
    env = os.environ.copy()
    env['MRC_BRIDGE_CONFIG'] = str(bridge_dir / 'config.example.json')
    
    # Use a test port
    config_content = (bridge_dir / 'config.example.json').read_text()
    config_content = config_content.replace('"web_listen_port": 8080', '"web_listen_port": 18080')
    
    test_config = bridge_dir / 'config.test.json'
    test_config.write_text(config_content)
    env['MRC_BRIDGE_CONFIG'] = str(test_config)
    
    proc = subprocess.Popen(
        [sys.executable, '-m', 'services.mrc_bridge.run'],
        cwd=str(Path(__file__).parent.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Give it time to start
    time.sleep(2)
    
    # Check if process is still running
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        pytest.fail(f"Bridge failed to start:\nSTDOUT: {stdout.decode()}\nSTDERR: {stderr.decode()}")
    
    yield proc
    
    # Cleanup
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    
    # Remove test config
    if test_config.exists():
        test_config.unlink()


@pytest.fixture
def app():
    """Create Flask app for testing"""
    from anetbbs.web_app import create_app
    
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    # Set test bridge config
    app.config['MRC_BRIDGE_HOST'] = 'localhost'
    app.config['MRC_BRIDGE_PORT'] = 18080
    app.config['MRC_BRIDGE_USE_SSL'] = False
    app.config['MRC_BRIDGE_WS_PATH'] = '/ws'
    
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def authenticated_client(client, app):
    """Create authenticated test client"""
    from anetbbs.models import User, db
    
    with app.app_context():
        # Create test user
        user = User(username='testuser', email='test@example.com')
        user.set_password('password')
        db.session.add(user)
        db.session.commit()
        
        # Login
        client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'password'
        })
    
    yield client


def test_bridge_service_starts(bridge_service):
    """Test that bridge service can start"""
    assert bridge_service.poll() is None, "Bridge service should be running"


def test_mrc_page_requires_auth(client):
    """Test that MRC page requires authentication"""
    response = client.get('/mrc/')
    # Should redirect to login
    assert response.status_code == 302
    assert '/auth/login' in response.location


def test_mrc_page_loads_authenticated(authenticated_client):
    """Test that MRC page loads for authenticated users"""
    response = authenticated_client.get('/mrc/')
    assert response.status_code == 200
    assert b'MRC Chat' in response.data
    assert b'testuser' in response.data  # Should show username as suggested handle


def test_mrc_page_has_websocket_config(authenticated_client):
    """Test that MRC page includes WebSocket configuration"""
    response = authenticated_client.get('/mrc/')
    assert response.status_code == 200
    # Should have WebSocket URL
    assert b'ws://localhost:18080/ws' in response.data or b'WS_URL' in response.data


def test_bridge_websocket_endpoint(bridge_service):
    """Test that bridge WebSocket endpoint is available"""
    try:
        import asyncio
        import aiohttp
        
        async def test_ws():
            session = aiohttp.ClientSession()
            try:
                ws = await session.ws_connect('http://localhost:18080/ws')
                
                # Should receive welcome message
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                assert msg.type == aiohttp.WSMsgType.TEXT
                
                data = msg.json()
                assert data['type'] == 'welcome'
                
                await ws.close()
            finally:
                await session.close()
        
        asyncio.run(test_ws())
    except ImportError:
        pytest.skip("aiohttp not installed")
    except Exception as e:
        pytest.fail(f"WebSocket connection failed: {e}")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
