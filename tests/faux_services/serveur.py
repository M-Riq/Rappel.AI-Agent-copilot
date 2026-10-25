"""Serveur HTTP local commun aux faux services (127.0.0.1, port éphémère, fil d'exécution dédié)."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class GestionnaireJSON(BaseHTTPRequestHandler):
    """Gestionnaire minimal : le faux service fournit `traiter(methode, chemin, entetes, corps)`."""

    service = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence : aucun en-tête (jeton) dans la sortie des tests
        pass

    def _repondre(self, methode):
        longueur = int(self.headers.get("Content-Length") or 0)
        brut = self.rfile.read(longueur) if longueur else b""
        try:
            corps = json.loads(brut) if brut else None
        except ValueError:
            corps = brut
        statut, donnees, entetes = self.service.traiter(methode, self.path, dict(self.headers), corps)
        contenu = donnees if isinstance(donnees, bytes) else json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", (entetes or {}).pop("Content-Type", "application/json; charset=utf-8"))
        self.send_header("Content-Length", str(len(contenu)))
        self.send_header("X-Faux-Service", "rappel-tests")
        for nom, valeur in (entetes or {}).items():
            self.send_header(nom, valeur)
        self.end_headers()
        self.wfile.write(contenu)

    def do_GET(self):
        self._repondre("GET")

    def do_POST(self):
        self._repondre("POST")


class ServeurLocal:
    """Contexte : démarre le faux service sur 127.0.0.1:<port éphémère>, l'arrête à la sortie."""

    def __enter__(self):
        gestionnaire = type("Gestionnaire", (GestionnaireJSON,), {"service": self})
        self._serveur = ThreadingHTTPServer(("127.0.0.1", 0), gestionnaire)
        self._serveur.daemon_threads = True
        self.port = self._serveur.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._fil = threading.Thread(target=self._serveur.serve_forever, daemon=True)
        self._fil.start()
        return self

    def __exit__(self, *exception):
        self._serveur.shutdown()
        self._serveur.server_close()
        self._fil.join(timeout=5)

    def traiter(self, methode, chemin, entetes, corps):  # pragma: no cover - surchargé
        raise NotImplementedError
