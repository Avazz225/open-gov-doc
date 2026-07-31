# 0020 — Polling statt Push für die SLA-Zeitüberwachung (Timer/Boundary Events)

**Status:** akzeptiert
**Kontext:** P6-S2 (SLA-Zeitüberwachung je Schritt, Konzept 7.1). SpiffWorkflow feuert fällige Timer/Boundary Events nicht von selbst (kein Hintergrund-Thread, kein Push-Mechanismus) - ein Aufrufer muss `BpmnWorkflow.refresh_waiting_tasks()` aktiv aufrufen, damit ein fälliger `WAITING`-Timer-Task nach `READY` wechselt und per `do_engine_steps()` ausgeführt wird (siehe `spiff_adapter.py`-Docstring, gegen die installierte Version 3.1.2 verifiziert). `workflow-service` braucht daher einen eigenen Mechanismus, der das regelmäßig für jede laufende Prozessinstanz anstößt.

## Entscheidung

Ein einzelner asyncio-Hintergrund-Task innerhalb von `workflow-service`s `lifespan` (`_sla_poll_loop`) prüft in festem Intervall (`sla_poll_interval_seconds`, Default 30s) **alle** Instanzen mit `status="running"`: deserialisieren, `spiff_adapter.check_timers()` (kapselt `refresh_waiting_tasks()`+`do_engine_steps()`), Blob neu persistieren, gefeuerte Boundary-Events als `workflow.task.escalated` publizieren. Kein separater Scheduler-Dienst, keine verteilte Sperre zwischen mehreren `workflow-service`-Instanzen.

## Begründung

- **Kein Push-fähiger Scheduler im Projekt vorhanden** - weder SpiffWorkflow noch das Projekt selbst bringen einen Hintergrund-Thread/Scheduler mit. Ein Polling-Loop innerhalb desselben Prozesses ist der einfachste Mechanismus, der ohne neue Infrastruktur (Celery Beat, APScheduler, Cron-Container) auskommt - konsistent mit dem Grundsatz, keine Abstraktion über das für ein Grundgerüst Nötige hinaus einzuführen.
- **ADR 0019 hat die Konsequenz bereits akzeptiert**: da jede Instanz ihren vollständigen Zustand als serialisierten Blob speichert (keine normalisierte Task-Tabelle), erfordert jede Cross-Instanz-Abfrage ("welche Instanzen haben fällige Timer?") eine Deserialisierung jeder laufenden Instanz. Ein Polling-Tick tut genau das - keine neue, sondern dieselbe bereits dokumentierte Einschränkung.
- **Präzision ist explizit an das Poll-Intervall gekoppelt**, nicht an die exakte Timer-Fälligkeit - für ein Grundgerüst akzeptabel, da Konzept 7.1 keine Echtzeit-Anforderung an die Eskalationserkennung stellt (im Gegensatz zu z. B. einer Signatur-Frist).

## Konsequenzen

- **SLA-Erkennungsverzögerung bis zu `sla_poll_interval_seconds`**: ein Timer, der kurz nach einem Tick fällig wird, wird erst beim nächsten Tick erkannt. Bei sehr kurzen SLA-Fristen (unterhalb des Poll-Intervalls) müsste das Intervall projektweit verkleinert werden - pro-Prozess-konfigurierbare Intervalle sind nicht vorgesehen.
- **Keine verteilte Sperre**: läuft `workflow-service` horizontal skaliert (mehrere Replikate), pollt jedes Replikat unabhängig dieselben Instanzen und würde bei einem fälligen Timer denselben `workflow.task.escalated` mehrfach publizieren (Notification Service würde dieselbe Eskalation mehrfach zustellen). Das Projekt geht aktuell durchgängig von Einzelinstanz-Deployments aus (kein anderer Service löst das anders); bei Bedarf horizontaler Skalierung dieses Service müsste eine Leader-Election oder eine DB-seitige Sperre (`SELECT ... FOR UPDATE SKIP LOCKED` je Instanz) nachgezogen werden.
- **Jede laufende Instanz wird bei jedem Tick vollständig deserialisiert**, unabhängig davon, ob sie überhaupt einen wartenden Timer hat - bei sehr vielen gleichzeitig laufenden Instanzen wird das zum Skalierungsengpass. Eine effizientere Lösung (z. B. eine aus dem Blob abgeleitete "nächste Fälligkeit"-Projektionsspalte, nach der gefiltert werden kann) ist ein möglicher künftiger Optimierungsschritt, hier bewusst nicht vorgezogen (kein aktueller Bedarf, siehe ADR 0019).
