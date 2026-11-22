from unittest.mock import patch
import threading
from u2c_customfw.web.device_events import DeviceEvents


def test_notification_generation_and_shutdown():
    with patch('u2c_customfw.web.device_events.sys.platform', 'test'):
        events = DeviceEvents()
    assert events.wait(-1) == {'generation': 0, 'available': False}
    events.available = True
    result = []
    worker = threading.Thread(target=lambda: result.append(events.wait(0)))
    worker.start()
    events.changed()
    worker.join(1)
    assert result == [{'generation': 1, 'available': True}]
    assert events.wait(0)['generation'] == 1
    worker = threading.Thread(target=lambda: result.append(events.wait(1)))
    worker.start()
    events.close()
    worker.join(1)
    assert not worker.is_alive()
