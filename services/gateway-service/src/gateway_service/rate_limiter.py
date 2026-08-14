import time
import uuid

from redis import asyncio as redis


class RateLimiter:
    """Sliding-Window-Rate-Limiting je Client (3.5) - geteilter Redis-Store
    statt in-process `dict` (siehe ADR 0005 für den ursprünglichen
    In-Prozess-Entwurf und ADR 0097 für diese Umstellung): mehrere horizontal
    skalierte Gateway-Instanzen sehen denselben Zähler je Client-Schlüssel,
    statt jeweils ihr eigenes, unabhängiges Limit zu führen.

    Sliding Window via Sorted Set (`ZADD`/`ZREMRANGEBYSCORE`/`ZCARD`) statt der
    einfacheren Fixed-Window-Variante (`INCR`+`EXPIRE`): ein Fixed-Window
    erlaubt an der Fensterkante kurzzeitig bis zu 2x `max_requests` (Ende von
    Fenster N und Anfang von Fenster N+1 fallen für einen Client zeitlich
    zusammen) - für einen Login-Schutz (Brute-Force, gilt auch für öffentliche
    Routen, siehe `docs/services/gateway-service.md`) eine reale Schwäche. Der
    Preis dafür ist ein Sorted-Set-Member pro Request statt eines einzelnen
    Zählers - bei den hier üblichen Fenstern (Default 600 Requests/60s je
    Client) vernachlässigbar gegenüber der saubereren Garantie.
    """

    def __init__(self, *, redis_url: str, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        redis_key = f"gateway:rate-limit:{key}"
        # uuid-Suffix statt reinem Zeitstempel als Member: zwei Requests
        # innerhalb derselben Fließkomma-Auflösung dürfen sich in der Sorted
        # Set nicht gegenseitig überschreiben (sonst zählt Redis sie als
        # einen einzigen Member).
        member = f"{now:.6f}:{uuid.uuid4().hex}"

        # Eine einzelne MULTI/EXEC-Transaktion: alte Einträge entfernen, neuen
        # hinzufügen, danach zählen - der neue Eintrag ist im Zählergebnis
        # bereits enthalten. Bei Überschreitung wird er gleich im Anschluss
        # per ZREM wieder entfernt (siehe unten), statt ihn vorab bedingt
        # hinzuzufügen - Redis kennt kein bedingtes ZADD "nur falls ZCARD
        # danach <= max_requests", ein serverseitiges Lua-Skript wäre die
        # einzige Alternative, hier bewusst nicht gewählt (mehr bewegliche
        # Teile für denselben Effekt).
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, window_start)
            pipe.zadd(redis_key, {member: now})
            pipe.zcard(redis_key)
            # TTL als Sicherheitsnetz, falls ein Client dauerhaft verschwindet
            # (kein weiterer Request räumt den Key dann sonst nie ab) - etwas
            # großzügiger als das Fenster selbst, damit ein knapp
            # laufender Request die Historie nicht vorzeitig verliert.
            pipe.expire(redis_key, int(self.window_seconds) + 1)
            _, _, count, _ = await pipe.execute()

        if count > self.max_requests:
            await self._redis.zrem(redis_key, member)
            return False
        return True

    async def aclose(self) -> None:
        await self._redis.aclose()
