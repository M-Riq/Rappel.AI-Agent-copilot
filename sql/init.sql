CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    signature TEXT,
    texte TEXT,
    statut TEXT,
    preuves JSONB,
    confiance REAL,
    raison_rejet TEXT,
    cree_le TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memoire_incidents (
    id SERIAL PRIMARY KEY,
    signature TEXT,
    cause_confirmee TEXT,
    hypotheses_refutees JSONB,
    correctif_essaye TEXT,
    resultat TEXT,
    date DATE
);
