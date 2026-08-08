from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "plugin-orchestration-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    permission_service_base_url: str = "http://localhost:8004"
    auth_service_base_url: str = "http://localhost:8003"

    orchestration_permission: str = "admin.orchestration"

    # Eigene, bewusst minimale Ressourcen-Stichprobe (3.8, P10-S0-Befund: die
    # vollwertige Sensor-Infrastruktur aus 10.1 existiert erst Phase 11).
    # Sampelt periodisch per `psutil` ausschliesslich den eigenen Host - in
    # der real existierenden Docker-Compose-Umgebung gibt es ohnehin nur
    # diesen einen Knoten.
    resource_sample_interval_seconds: float = 30.0

    # Ab wann gilt ein `PluginResourceReport` als veraltet und zaehlt weder
    # fuer die Median-Schaetzung noch fuer die Singleton-Konflikterkennung.
    resource_report_stale_after_seconds: float = 60.0

    # Cold-Start-Platzierung ohne jede Beobachtung (3.8: "statische
    # Richtwerte ... oder ersatzweise der Median der beobachteten
    # Ressourcennutzung") - falls beides fehlt (weder Manifest-Werte noch
    # irgendein frischer Resource-Report desselben Typs), dieser dokumentierte
    # Minimal-Default statt eines Fehlers.
    default_cpu_cores: float = 0.5
    default_ram_mb: float = 256.0

    registry_dependency_cache_ttl_seconds: float = 15.0
