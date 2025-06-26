import json
import asyncio
import aiohttp
from urllib.parse import urlparse
import threading
import requests
import time

# ha.py takes care of all the communication with Home Assistant, while tui.py is the interface.
# backend and frontend I think is what all the computer programbers in sillicone vally call that

# I had a look at home-assistant-cli and considered bodging together a frontend for it,
# and even tried stealing some of its code and doing REST calls, but I couldn't get it
# to work, so this is all websocket. I hope it doesn't break. I may come to regret this...

# You should check out the CLI. It's very featureful.
# https://github.com/home-assistant-ecosystem/home-assistant-cli

DEBUG_LOGGING = True  # if set TRUE, this will dump a hatui_debug.log in the cwd with all keypresses & actions

class HAApiError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details

class HA:
    def __init__(self, url, token):
        self.url = url.rstrip('/')
        self.token = token
        self.session = requests.Session()
        self._ws_lock = threading.Lock()
        self._ws_next_id = 1
        self.ws_url = f"{self.url}/api/websocket"

    def _run_async(self, coro):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    async def _ws_request(self, message_type, **kwargs):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.ws_url) as ws:
                initial_response = await ws.receive()
                if initial_response.type == aiohttp.WSMsgType.TEXT:
                    initial_data = json.loads(initial_response.data)
                    if initial_data.get('type') != 'auth_required':
                        raise HAApiError(f"Expected auth_required, got: {initial_data}")
                else:
                    raise HAApiError(f"Unexpected initial response type: {initial_response.type}")
                
                auth_msg = {
                    'type': 'auth',
                    'access_token': self.token
                }
                await ws.send_str(json.dumps(auth_msg))
                
                auth_response = await ws.receive()
                if auth_response.type == aiohttp.WSMsgType.TEXT:
                    auth_data = json.loads(auth_response.data)
                    if auth_data.get('type') == 'auth_invalid':
                        raise HAApiError(f"Authentication failed: {auth_data.get('message', 'Unknown error')}")
                    elif auth_data.get('type') != 'auth_ok':
                        raise HAApiError(f"Unexpected auth response: {auth_data}")
                else:
                    raise HAApiError(f"Unexpected auth response type: {auth_response.type}")
                
                request_msg = {
                    'id': 1,
                    'type': message_type,
                    **kwargs
                }
                await ws.send_str(json.dumps(request_msg))
                
                response = await ws.receive()
                if response.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(response.data)
                    if data.get('type') == 'result':
                        return data.get('result')
                    elif data.get('type') == 'error':
                        raise HAApiError(f"API error: {data.get('error', {}).get('message', 'Unknown error')}")
                    else:
                        raise HAApiError(f"Unexpected response type: {data.get('type')}")
                else:
                    raise HAApiError("WebSocket connection error")

    def list_entities(self):
        try:
            entities = self._run_async(self._ws_request('config/entity_registry/list'))
            if entities:
                entity_ids = [entity['entity_id'] for entity in entities]
                return entity_ids
            else:
                return []
        except Exception as e:
            raise HAApiError(f"Failed to list entities: {e}", details=str(e))

    def get_sensor_state(self, entity_id):
        try:
            states = self._run_async(self._ws_request('get_states'))
            if states:
                for state in states:
                    if state.get('entity_id') == entity_id:
                        return state
                raise HAApiError(f"Entity {entity_id} not found")
            else:
                raise HAApiError("No states returned from WebSocket API")
        except Exception as e:
            raise HAApiError(f"Failed to get state for {entity_id}: {e}", details=str(e))

    def log_debug(self, message):
        if DEBUG_LOGGING:
            with open('hatui_debug.log', 'a') as f:
                print(f"DEBUG: {message}", file=f)

    def _ensure_ws(self):
        if not hasattr(self, '_ws_loop') or not self._ws_loop.is_running():
            self._ws_loop = asyncio.new_event_loop()
            self._ws_thread = threading.Thread(target=self._ws_loop.run_forever, daemon=True)
            self._ws_thread.start()
        if not hasattr(self, '_ws_client') or self._ws_client is None or self._ws_client.closed:
            import random
            import json
            async def connect():
                session = aiohttp.ClientSession()
                ws = await session.ws_connect(f"{self.url.replace('http','ws')}/api/websocket")
                msg = await ws.receive_json()
                if msg.get('type') != 'auth_required':
                    raise Exception('WebSocket auth_required not received')
                await ws.send_json({'type': 'auth', 'access_token': self.token})
                msg = await ws.receive_json()
                if msg.get('type') != 'auth_ok':
                    raise Exception('WebSocket auth failed')
                return ws, session
            fut = asyncio.run_coroutine_threadsafe(connect(), self._ws_loop)
            self._ws_client, self._ws_session = fut.result()

    def _ws_command(self, msg):
        import asyncio
        import time
        with self._ws_lock:
            for attempt in range(2):
                try:
                    msg_id = self._ws_next_id
                    self._ws_next_id += 1
                    msg['id'] = msg_id
                    async def send_and_wait(ws):
                        await ws.send_json(msg)
                        start = time.time()
                        while True:
                            resp = await ws.receive_json()
                            if resp.get('id') == msg_id:
                                return resp
                            if time.time() - start > 5:
                                raise Exception(f"Timeout waiting for response to id {msg_id}")
                    fut = asyncio.run_coroutine_threadsafe(send_and_wait(self._ws_client), self._ws_loop)
                    return fut.result()
                except Exception as e:
                    self._ws_client = None
                    self._ensure_ws()
                    if attempt == 1:
                        raise

    def toggle_entity_ws(self, entity_id):
        self._ensure_ws()
        domain = 'switch' if entity_id.startswith('switch.') else 'light' if entity_id.startswith('light.') else None
        if not domain:
            raise ValueError('toggle_entity_ws only works for switch.* or light.* entities')
        msg = {
            'type': 'call_service',
            'domain': domain,
            'service': 'toggle',
            'service_data': {'entity_id': entity_id},
        }
        return self._ws_command(msg)

    def turn_on_entity_ws(self, entity_id):
        self._ensure_ws()
        domain = 'switch' if entity_id.startswith('switch.') else 'light' if entity_id.startswith('light.') else None
        if not domain:
            raise ValueError('turn_on_entity_ws only works for switch.* or light.* entities')
        msg = {
            'type': 'call_service',
            'domain': domain,
            'service': 'turn_on',
            'service_data': {'entity_id': entity_id},
        }
        return self._ws_command(msg)

    def turn_off_entity_ws(self, entity_id):
        self._ensure_ws()
        domain = 'switch' if entity_id.startswith('switch.') else 'light' if entity_id.startswith('light.') else None
        if not domain:
            raise ValueError('turn_off_entity_ws only works for switch.* or light.* entities')
        msg = {
            'type': 'call_service',
            'domain': domain,
            'service': 'turn_off',
            'service_data': {'entity_id': entity_id},
        }
        return self._ws_command(msg)
