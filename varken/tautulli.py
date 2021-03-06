import logging
from requests import Session, Request
from datetime import datetime, timezone
from geoip2.errors import AddressNotFoundError

from varken.helpers import geo_lookup, hashit, connection_handler
from varken.structures import TautulliStream


class TautulliAPI(object):
    def __init__(self, server, dbmanager):
        # Set Time of initialization
        self.now = datetime.now(timezone.utc).astimezone().isoformat()
        self.dbmanager = dbmanager
        self.server = server
        self.session = Session()
        self.session.params = {'apikey': self.server.api_key, 'cmd': 'get_activity'}
        self.endpoint = '/api/v2'
        self.logger = logging.getLogger()

    def __repr__(self):
        return "<tautulli-{}>".format(self.server.id)

    def get_activity(self):
        self.now = datetime.now(timezone.utc).astimezone().isoformat()
        influx_payload = []

        req = self.session.prepare_request(Request('GET', self.server.url + self.endpoint))
        g = connection_handler(self.session, req, self.server.verify_ssl)

        if not g:
            return

        get = g['response']['data']

        try:
            sessions = [TautulliStream(**session) for session in get['sessions']]
        except TypeError as e:
            self.logger.error('TypeError has occurred : %s while creating TautulliStream structure', e)
            return

        for session in sessions:
            try:
                geodata = geo_lookup(session.ip_address_public)
            except (ValueError, AddressNotFoundError):
                if self.server.fallback_ip:
                    geodata = geo_lookup(self.server.fallback_ip)
                else:
                    my_ip = self.session.get('http://ip.42.pl/raw').text
                    geodata = geo_lookup(my_ip)

            if not all([geodata.location.latitude, geodata.location.longitude]):
                latitude = 37.234332396
                longitude = -115.80666344
            else:
                latitude = geodata.location.latitude
                longitude = geodata.location.longitude

            decision = session.transcode_decision
            if decision == 'copy':
                decision = 'direct stream'

            video_decision = session.stream_video_decision
            if video_decision == 'copy':
                video_decision = 'direct stream'
            elif video_decision == '':
                video_decision = 'Music'

            quality = session.stream_video_resolution
            if not quality:
                quality = session.container.upper()
            elif quality in ('SD', 'sd', '4k'):
                quality = session.stream_video_resolution.upper()
            else:
                quality = session.stream_video_resolution + 'p'

            player_state = session.state.lower()
            if player_state == 'playing':
                player_state = 0
            elif player_state == 'paused':
                player_state = 1
            elif player_state == 'buffering':
                player_state = 3

            product_version = session.product_version
            if session.platform == 'Roku':
                product_version = session.product_version.split('-')[0]

            hash_id = hashit('{}{}{}{}'.format(session.session_id, session.session_key, session.username,
                                               session.full_title))
            influx_payload.append(
                {
                    "measurement": "Tautulli",
                    "tags": {
                        "type": "Session",
                        "session_id": session.session_id,
                        "friendly_name": session.friendly_name,
                        "username": session.username,
                        "title": session.full_title,
                        "platform": session.platform,
                        "product_version": product_version,
                        "quality": quality,
                        "video_decision": video_decision.title(),
                        "transcode_decision": decision.title(),
                        "media_type": session.media_type.title(),
                        "audio_codec": session.audio_codec.upper(),
                        "audio_profile": session.audio_profile.upper(),
                        "stream_audio_codec": session.stream_audio_codec.upper(),
                        "quality_profile": session.quality_profile,
                        "progress_percent": session.progress_percent,
                        "region_code": geodata.subdivisions.most_specific.iso_code,
                        "location": geodata.city.name,
                        "full_location": '{} - {}'.format(geodata.subdivisions.most_specific.name,
                                                          geodata.city.name),
                        "latitude": latitude,
                        "longitude": longitude,
                        "player_state": player_state,
                        "device_type": session.platform,
                        "server": self.server.id
                    },
                    "time": self.now,
                    "fields": {
                        "hash": hash_id
                    }
                }
            )

        influx_payload.append(
            {
                "measurement": "Tautulli",
                "tags": {
                    "type": "current_stream_stats",
                    "server": self.server.id
                },
                "time": self.now,
                "fields": {
                    "stream_count": int(get['stream_count']),
                    "total_bandwidth": int(get['total_bandwidth']),
                    "wan_bandwidth": int(get['wan_bandwidth']),
                    "lan_bandwidth": int(get['lan_bandwidth']),
                    "transcode_streams": int(get['stream_count_transcode']),
                    "direct_play_streams": int(get['stream_count_direct_play']),
                    "direct_streams": int(get['stream_count_direct_stream'])
                }
            }
        )

        self.dbmanager.write_points(influx_payload)
