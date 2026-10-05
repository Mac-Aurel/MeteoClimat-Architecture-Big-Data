# Dossier d'architecture Big Data — Épisodes extrêmes

Oct 4, 2026 · @mxdukpè

## 1. Le projet en une page

Nous construisons une plateforme qui transforme des millions de mesures météo quotidiennes en un **catalogue d'épisodes extrêmes** (vagues de chaleur, vagues de froid, fortes pluies), enrichi des records battus et d'indicateurs locaux, puis une IA qui regroupe les épisodes et repère les événements atypiques.

| Élément imposé | Ce que nous livrons concrètement |
| --- | --- |
| Données | Le portail open data Infoclimat / Météo-France (portail.chom.engineering) : 39 milliards de mesures, archives depuis 1777, API JSON sans clé |
| Thème | Épisodes extrêmes de température et de précipitation, France métropolitaine |
| Produit Gold | Une table `episodes` (un épisode = une ligne), une table `records`, une table `indicateurs_locaux` par station et par année |
| Application IA | 1) détection de journées atypiques par station ; 2) regroupement des journées atypiques en épisodes spatio-temporels ; 3) typologie des épisodes par clustering |
| Évaluation | Qualité de détection contre un jeu de référence (les vagues de chaleur officielles Météo-France + les records battus), puis cohérence spatiale et temporelle des épisodes |

**L'idée clé à retenir pour la soutenance :** une mesure brute (« il a fait 38 °C à Lyon ») ne dit rien seule. Elle devient un *événement* quand on la compare à la normale du lieu et de la saison, et un *épisode* quand plusieurs événements proches dans l'espace et le temps sont regroupés. Toute l'architecture sert à faire ce passage : mesure → anomalie → événement → épisode.

**Périmètre conseillé :** 1991 → aujourd'hui, environ 200 à 300 stations Météo-France de bonne qualité, variables `tn`, `tx`, `tm`, `rr`. C'est assez gros pour justifier une architecture Big Data, assez petit pour tenir dans un projet d'école.

## 2. Comprendre les données du portail

Le portail publie 27 datasets déjà agrégés (journalier, mensuel, records), regroupés en familles d'API ; ils sont eux-mêmes la couche « Gold » d'Infoclimat, et deviennent **notre couche Bronze**. Point important : nous ne téléchargeons pas les 39 milliards de mesures horaires (accès sur token), nous partons des agrégats journaliers.

### 2.1 Les familles d'API

| Famille | Ce qu'elle contient | Identifiant station | Utile pour nous ? |
| --- | --- | --- | --- |
| `/v2/` | Climatologie recalculée avec contrôle qualité, unités °C et mm. Choix par défaut recommandé par le portail | `ic_id` (+ `mfid`) | **Oui, source principale** |
| `/dataclimat/` | Données Météo-France seules : records absolus, records battus, normales 1991-2020, ITN | `station_code` (= `mfid`) | **Oui** (normales, records, référence) |
| `/ref/` | Référentiel : coordonnées, altitude, département, paramètres mesurés | `ic_id`, `station_uid` | **Oui** (dimension station, indispensable pour le spatial) |
| `/canicule/` | Indicateurs d'été extrême par commune / EPCI / département | code INSEE / SIREN | Optionnel (contrôle croisé) |
| `/legacy/` | Même contenu que v2 au format Météo-France d'origine (`NUM_POSTE`, `AAAAMM`…) | `mfid` | Non (doublon de v2) |
| `/ic/` | Collecte Infoclimat hors Météo-France (synop, metar, bouées, réseau participatif) | `ic_id` | Non en V1 (qualité hétérogène) |
| `/statIC/` | Réseau participatif seul, avec indicateurs de densité | `ic_id` | Non en V1, piste d'extension |

### 2.2 Les colonnes à connaître par cœur

- `tn` / `tx` : température minimale / maximale du jour (°C). `tm` = (tn + tx) / 2.
- `rr` : cumul de pluie du jour (mm).
- `n_obs` / `n_obs_rejetees` : nombre d'observations du jour et nombre rejetées par le contrôle qualité. C'est la « preuve » derrière tn/tx.
- `statut_extremes` : `quotidien` (≥ 8 observations retenues, extrême fiable), `partiel` (2 à 7), `releve_unique` (1 seule mesure, ce n'est **pas** un extrême).
- `baseline_mean_tntxm` : normale 1991-2020 de la température moyenne, par station et par jour de l'année.
- `itn` : Indicateur Thermique National, moyenne quotidienne de 30 stations de référence.
- `records_battus` : une ligne par date où une station a établi un nouveau record chaud (TX) ou froid (TN).

### 2.3 Identifiants : le piège n°1

Trois identifiants coexistent. `ic_id` fait foi (formats hétérogènes : `07486`, `FMEE`, `STATIC0253`). `mfid` est le numéro de poste Météo-France à 8 chiffres, appelé `station_code` dans `/dataclimat/` et `NUM_POSTE` dans `/legacy/`. `station_uid` est un hash dérivé de `ic_id`. **Décision :** en Silver, une table de correspondance unique `dim_station` (ic\_id ↔ mfid) sert à toutes les jointures.

### 2.4 Contraintes d'accès

- API REST ouverte, sans clé, **20 000 lignes maximum par requête**. Il faut tester le champ `tronque` et paginer avec `suite.from`, sinon on obtient des séries incomplètes sans erreur.
- Une station sur 35 ans ≈ 12 800 jours : une seule requête suffit généralement par station et par dataset.
- Le service tourne sur une seule machine : timeout 15 s, un retry après 2 s sur un code 503, et on reste raisonnable sur la parallélisation.
- Accès bulk Parquet/Iceberg (la table journalière complète fait 145 millions de lignes) sur demande d'identifiants S3. À demander si l'école veut un vrai passage à l'échelle.
- Mise à jour quotidienne (J-1, dès 08:00 UTC) : on peut prévoir une ingestion incrémentale.

### 2.5 Pièges connus à citer dans le dossier

- Un `tx` calculé sur 2 observations sous-estime le vrai maximum (jusqu'à 5 °C d'amplitude perdue). On filtre ou on pondère avec `statut_extremes`.
- La densité de mesure a énormément changé : médiane de 4 observations par jour en 1960 contre 24 en 2025. Un détecteur naïf trouverait « plus d'extrêmes » récemment juste parce qu'on mesure mieux.
- Les records `/dataclimat/` ne gardent que les stations de classe de site ≤ 3 : on y trouve Pontarlier −32,0 °C plutôt que Mouthe −36,7 °C.
- Les normales n'existent que pour `tntxm` (température moyenne) et les stations de classe ≤ 4 : pour tx, tn et rr, **nous calculons nos propres seuils** (section 4.3).
- Les erreurs suivent la norme RFC 9457 : on lit le champ `code`. Une station sans donnée renvoie 200 avec `n = 0`, ce n'est pas une erreur.

## 3. Datasets retenus et pourquoi

Nous retenons 7 endpoints : un pour les mesures, deux pour les références (normales, référentiel), trois pour les records, un pour la vérité terrain nationale.

| Endpoint | Rôle dans le projet | Pourquoi lui plutôt qu'un autre |
| --- | --- | --- |
| `/v2/journaliere` | Table de faits principale : tn, tx, tm, rr par station et par jour | Contrôle qualité déjà appliqué, unités simples, champs de qualité (`n_obs`, `statut_extremes`). Legacy apporte la même chose sans ces garanties |
| `/ref/stations` | Dimension station : latitude, longitude, altitude, département | Sans coordonnées, aucune analyse spatiale ni regroupement d'épisodes possible |
| `/dataclimat/baseline_1991_2020` | Normale journalière par station | Référence officielle pour calculer l'anomalie de température moyenne |
| `/v2/records` | Records chaud / froid par station et par mois calendaire | Donne le record et la 2e valeur : un grand écart signale un extrême isolé (suspect) |
| `/dataclimat/records_battus` | Historique des records battus, daté | Événements « certifiés » : sert de jeu de référence local pour l'évaluation |
| `/v2/itn` et `/dataclimat/itn_baseline` | Température moyenne nationale quotidienne et sa normale | Permet de reconstruire la liste officielle des vagues de chaleur Météo-France = vérité terrain nationale |
| `/ref/station-parametre` | Profondeur des séries par station | Sélectionner les stations ayant des séries longues et continues |

**Critères de sélection des stations (à justifier dans le dossier) :**

1. Station Météo-France (mfid renseigné) en France métropolitaine.
2. Série température disponible au moins depuis 1991 (pour calculer les seuils sur 1991-2020, la période de référence officielle actuelle).
3. Moins de 10 % de jours manquants sur la période.
4. Répartition spatiale : au moins 2 stations par département quand c'est possible, pour pouvoir tester la cohérence spatiale.

**Ce que nous écartons volontairement :** les données horaires (sur token, et inutiles pour des épisodes de plusieurs jours), le réseau participatif StatIC (capteurs hétérogènes, classe de site non renseignée) et `/legacy/` (doublon). Ce sont des pistes d'amélioration à mentionner, pas des oublis.

## 4. Architecture médaillon

L'architecture médaillon range les données en trois couches de qualité croissante — Bronze (brut), Silver (propre), Gold (métier) — pour que chaque étape soit rejouable, contrôlable et explicable.

&#91;embedded content: architecture médaillon du projet · source, 3 couches, usages\]

On lit le schéma de haut en bas : la donnée ne remonte jamais, et chaque couche ne lit que la couche juste au-dessus.

### 4.1 Pourquoi trois couches plutôt qu'un seul script

- **Rejouabilité :** si on change la définition d'une vague de chaleur, on recalcule Silver et Gold depuis Bronze sans rappeler l'API.
- **Traçabilité :** chaque chiffre du catalogue peut être remonté jusqu'à la réponse JSON d'origine (date d'ingestion, requête).
- **Séparation des responsabilités :** Bronze = fidélité, Silver = qualité, Gold = sens métier. Un bug se localise dans une couche.
- **Performance :** les utilisateurs et l'IA lisent Gold, quelques milliers de lignes, pas des millions de mesures.

### 4.2 Le contenu de chaque couche

| Couche | Tables | Format et partitionnement | Règles |
| --- | --- | --- | --- |
| Bronze | `bronze/v2_journaliere`, `bronze/ref_stations`, `bronze/baseline`, `bronze/records`, `bronze/records_battus`, `bronze/itn` | JSON compressé, partition `dataset / date_ingestion / station` | Append-only, aucune transformation, on garde aussi les métadonnées de la réponse (`n`, `tronque`, `licence`) |
| Silver | `dim_station`, `mesures_journalieres`, `seuils_climato`, `itn_journalier`, `records_station` | Delta Lake (Parquet + journal de transactions), partition par `annee` | Schéma imposé, clé unique (station, date), drapeaux qualité, aucune ligne supprimée sans trace |
| Gold | `jours_atypiques`, `episodes`, `episode_stations`, `records`, `indicateurs_locaux` | Delta Lake, partition par `annee` et `type_evenement` | Une table = un usage, colonnes documentées dans un dictionnaire de données |

### 4.3 Les transformations Silver, dans l'ordre

1. **Typage et unités** : dates en `DATE`, températures en `DOUBLE` (°C), pluie en mm.
2. **dim\_station** : fusion de `/ref/stations` et de la correspondance ic\_id ↔ mfid, avec latitude, longitude, altitude, département.
3. **Dédoublonnage** sur la clé (station, date) : une pagination mal gérée crée des doublons.
4. **Drapeaux qualité** : `qualite_ok = true` si `statut_extremes = 'quotidien'`. Les jours `partiel` sont gardés mais marqués, les `releve_unique` sont exclus des calculs d'extrêmes.
5. **Calendrier complet** : une ligne par station et par jour, même sans mesure, pour mesurer les trous et ne pas « coller » deux épisodes séparés par une lacune.
6. **Seuils climatologiques** (`seuils_climato`) : pour chaque station et chaque jour de l'année, sur 1991-2020, avec une fenêtre glissante de 5 jours centrée (méthode des indices ETCCDI) — P90 de tx (chaud), P10 de tn (froid), P99 de rr sur les jours de pluie ≥ 1 mm.
7. **Anomalies** : `anom_tm = tm − normale`, `exces_tx = tx − P90`, `deficit_tn = P10 − tn`, `ratio_rr = rr / P99`, plus un z-score standardisé par station et par saison.

**Pourquoi des percentiles par station plutôt qu'un seuil fixe (ex. 35 °C) ?** 35 °C est banal à Carpentras et exceptionnel à Brest. Le percentile rend « extrême » relatif au climat local : c'est ce qui permet de comparer toutes les stations entre elles.

## 5. Stack technique et justification

Nous proposons une stack open source qui tourne en local avec Docker Compose et se transpose telle quelle sur un cloud (Databricks, AWS EMR, Azure) : c'est l'argument « scalable » du dossier.

| Brique | Outil proposé | Pourquoi | Alternative acceptable |
| --- | --- | --- | --- |
| Ingestion | Python (`requests`) + retry et pagination sur `suite.from` | L'API est REST/JSON, pas besoin d'outil lourd ; on maîtrise le respect du plafond et des 503 | Apache NiFi, Airbyte |
| Stockage (data lake) | MinIO (stockage objet compatible S3) | Même interface qu'AWS S3, gratuit, tourne en local | Disque local, S3 réel |
| Format de tables | Delta Lake | ACID, évolution de schéma, *time travel* (revenir à une version précédente pour l'audit) | Apache Iceberg (c'est celui de la source) |
| Traitement | Apache Spark (PySpark) | Distribué : la même logique passe de 300 stations à la table complète de 145 M lignes | DuckDB pour prototyper vite sur un portable |
| Orchestration | Apache Airflow (un DAG quotidien) | Planifie Bronze → Silver → Gold, gère les dépendances et les relances | Prefect, Dagster |
| Qualité des données | Great Expectations (ou tests SQL maison) | Contrôles automatiques entre couches (clés uniques, bornes physiques, complétude) | Soda, dbt tests |
| IA | scikit-learn (Isolation Forest, DBSCAN, KMeans), MLflow pour tracer les essais | Bibliothèques standard, explicables, suffisantes pour le volume Gold | PySpark MLlib |
| Restitution | Streamlit (carte + frise des épisodes) ou Apache Superset | Rapide à développer, carte interactive avec Folium / pydeck | Power BI, Grafana |

**Arguments de dimensionnement à mettre dans le dossier :**

- 300 stations × \~12 800 jours ≈ 3,8 millions de lignes journalières en Silver : gérable par Spark en local, et l'architecture ne change pas si l'on passe aux 145 millions de lignes de la table complète via l'accès Iceberg.
- Ingestion : environ 300 requêtes pour la journalière + quelques dizaines pour les référentiels et records. Avec 4 requêtes en parallèle maximum, l'ingestion complète prend quelques minutes, puis l'incrémental quotidien quelques secondes.
- Les 3 V du Big Data : **Volume** (millions de lignes, 235 ans d'archives possibles), **Variété** (séries, référentiels, records, sources multiples), **Vélocité** (mise à jour quotidienne).

## 6. Le produit Gold : catalogue, records, indicateurs locaux

Le produit Gold est un modèle en étoile : `episodes` est la table centrale, reliée aux stations par `episode_stations`, aux records battus par `records`, et résumée par station et par an dans `indicateurs_locaux`.

### 6.1 Du jour atypique à l'épisode : les définitions

| Type d'événement | Jour atypique (par station) | Épisode local | Source de la définition |
| --- | --- | --- | --- |
| Chaleur | `tx` > P90 de la station pour ce jour de l'année | ≥ 3 jours consécutifs | Indices ETCCDI (TX90p), durée alignée sur Météo-France |
| Froid | `tn` < P10 | ≥ 3 jours consécutifs | ETCCDI (TN10p) |
| Fortes pluies | `rr` > P99 des jours pluvieux | 1 jour suffit, fusion si jours voisins | ETCCDI (R99p) |
| Vague de chaleur nationale | ITN ≥ 25,3 °C au moins 1 jour et ≥ 23,4 °C au moins 3 jours | Fin si ITN < 23,4 °C plusieurs jours de suite ou < 22,4 °C | Définition officielle Météo-France |

Un **épisode régional** est un regroupement d'épisodes locaux proches dans l'espace et le temps. C'est le rôle de l'IA (section 7).

### 6.2 Tables Gold

**`jours_atypiques`** — une ligne par station, jour et type : `station_id`, `date`, `type_evenement`, `valeur`, `seuil`, `exces`, `score_atypicite` (IA), `qualite_ok`.

**`episodes`** — le catalogue, une ligne par épisode :

| Colonne | Signification |
| --- | --- |
| `episode_id` | Identifiant stable (type + date de début + numéro) |
| `type_evenement` | chaleur, froid, pluie |
| `date_debut`, `date_fin`, `duree_jours` | Bornes temporelles |
| `echelle` | locale (1 station), régionale, nationale |
| `n_stations`, `n_departements` | Étendue spatiale |
| `centroide_lat`, `centroide_lon`, `surface_km2` | Position et emprise (enveloppe convexe des stations touchées) |
| `intensite_max`, `station_max`, `date_max` | Valeur extrême atteinte, où et quand |
| `severite` | Somme des excès sur toutes les stations et tous les jours (en « degrés-jours » pour la chaleur) |
| `n_records_battus` | Nombre de records battus pendant l'épisode |
| `cluster_type` | Typologie issue du clustering (ex. « chaleur courte intense sud-est ») |
| `score_atypicite`, `est_atypique` | Score de l'épisode comparé aux autres épisodes du même type |
| `ref_officielle` | Vague de chaleur Météo-France correspondante, si elle existe (pour l'évaluation) |

**`episode_stations`** — table de liaison : quelles stations, quels jours, quel excès maximal par station.

**`records`** — les records battus (`/dataclimat/records_battus` + `/v2/records`) avec `ancienne_valeur`, `ecart` et le lien `episode_id` vers l'épisode pendant lequel ils sont tombés.

**`indicateurs_locaux`** — une ligne par station et par année :

- `nb_jours_tx90p`, `nb_jours_tn10p`, `nb_jours_rr99p` (jours atypiques) ;
- `nb_jours_tx_30`, `nb_jours_tx_35`, `nb_nuits_tropicales` (tn ≥ 20 °C) ;
- `tx_max_annuel`, `rr_max_1j` ;
- `nb_episodes`, `nb_records_battus`, `anomalie_tm_annuelle` ;
- `taux_completude` (part des jours mesurés), pour savoir si l'indicateur est fiable.

**Pourquoi ce modèle :** un décideur lit `episodes` (« quels événements, où, quand, quelle gravité ? »), un analyste local lit `indicateurs_locaux` (« ma commune a-t-elle plus de nuits tropicales qu'avant ? »), l'IA lit `jours_atypiques`. Chaque table répond à une question précise.

## 7. L'application IA : détection et regroupement

L'IA travaille en trois étages : elle repère les journées atypiques par station, les regroupe en épisodes spatio-temporels, puis classe les épisodes et signale ceux qui sortent de l'ordinaire. Les règles à seuil de la section 6 servent de **baseline** : l'IA doit faire au moins aussi bien qu'elles.

### 7.1 Étage A — Détecter les journées atypiques

Modèle : **Isolation Forest** (non supervisé), entraîné par saison sur les jours 1991-2020. Il isole les points « faciles à séparer » du reste : ce sont les anomalies. On n'a pas besoin d'étiquettes, ce qui est adapté car aucune base ne liste tous les jours extrêmes.

Variables d'entrée par station et par jour :

- `anom_tm`, `exces_tx`, `deficit_tn`, `ratio_rr` (anomalies par rapport au climat local) ;
- persistance : moyenne glissante sur 3 jours de l'anomalie ;
- amplitude `tx − tn` ;
- **écart aux voisins** : anomalie de la station moins la médiane des stations situées à moins de 100 km.

La dernière variable est décisive. Si tous les voisins sont chauds, c'est un événement météo ; si la station est seule à être à 40 °C, c'est probablement un capteur défectueux. Sortie : `score_atypicite` et un statut `atypique_meteo` / `suspect_capteur` / `normal`.

### 7.2 Étage B — Regrouper en épisodes

Modèle : **ST-DBSCAN** (DBSCAN spatio-temporel). Deux journées atypiques sont « voisines » si les stations sont à moins de `eps_km` et les dates à moins de `eps_jours`. Chaque groupe dense devient un épisode ; les points isolés restent des événements locaux.

- Valeurs de départ : `eps_km` = 150 km, `eps_jours` = 1 jour, `min_samples` = 3, séparément par type d'événement.
- Pourquoi DBSCAN plutôt que KMeans : on ne connaît pas à l'avance le nombre d'épisodes, les épisodes ont des formes quelconques, et DBSCAN sait laisser du « bruit » non regroupé.
- Variante simple et déterministe à présenter en comparaison : graphe où chaque station-jour atypique est un nœud, relié aux voisins dans l'espace et le temps ; un épisode = une composante connexe.
- Les paramètres sont réglés sur les vagues de chaleur officielles (section 8), puis gelés.

### 7.3 Étage C — Typologie et épisodes atypiques

Chaque épisode est décrit par : durée, nombre de stations, surface, intensité maximale, sévérité, mois de début, latitude et longitude du centroïde, nombre de records battus.

1. **Typologie :** KMeans sur ces variables standardisées, nombre de groupes choisi par le score de silhouette. Résultat attendu, par exemple : « vagues longues et étendues », « pics courts méditerranéens », « épisodes pluvieux cévenols ».
2. **Épisodes atypiques :** Isolation Forest (ou LOF) sur les mêmes variables. Un épisode est atypique s'il ne ressemble à aucun autre de son type : une vague de chaleur en octobre, ou une intensité record sur une courte durée.
3. **Explicabilité :** pour chaque épisode atypique, on affiche les 2-3 variables qui pèsent le plus (par exemple avec SHAP). Le jury doit comprendre pourquoi l'IA a signalé l'épisode.

**Pourquoi du non supervisé :** il n'existe pas d'étiquettes complètes pour tous les extrêmes locaux. Les références officielles disponibles (vagues de chaleur nationales, records battus) sont gardées pour l'évaluation, pas pour l'entraînement : c'est ce qui rend l'évaluation honnête.

## 8. Évaluation

On évalue deux choses distinctes : la **qualité de détection** (trouve-t-on les bons événements, sans fausses alertes ?) contre un jeu de référence, et la **cohérence spatio-temporelle** (les épisodes ont-ils une forme physiquement plausible ?), qui ne demande pas de référence.

### 8.1 Le jeu de référence

| Référence | Construction | Ce qu'elle permet de tester |
| --- | --- | --- |
| R1 — Vagues de chaleur nationales | Appliquer la définition officielle Météo-France à `/v2/itn`. Vérification : Météo-France recense 54 vagues de chaleur depuis 1947 (été 2026), notre reconstruction doit retrouver ce décompte | Détection des épisodes chauds à grande échelle, dates de début et de fin |
| R2 — Records battus | `/dataclimat/records_battus` (stations de classe ≤ 3) | Les jours de record doivent appartenir à un épisode ou être classés atypiques, et pas « suspects capteur » |
| R3 — Événements célèbres annotés à la main | 30 à 50 événements connus, dates et régions vérifiées dans les bilans Météo-France : canicule août 2003, vague de froid février 2012, épisodes cévenols (Gard 2002, Aude 2018), tempête Alex 2020, vagues de chaleur 2019, 2022, 2025… | Froid et pluie, que R1 ne couvre pas ; contrôle de l'emprise régionale |
| R4 — Périodes calmes | Périodes tirées au hasard sans alerte connue | Mesurer les fausses alertes |

**Règle anti-triche :** les paramètres (seuils, `eps_km`, `eps_jours`) sont réglés sur 1991-2010 et l'évaluation finale porte sur 2011 → aujourd'hui. Sinon on mesure la mémoire du modèle, pas sa qualité.

### 8.2 Métriques de détection

| Métrique | Définition simple | Objectif proposé |
| --- | --- | --- |
| Rappel (par épisode) | Part des vagues R1 / événements R3 retrouvés par au moins un épisode détecté qui les chevauche | ≥ 90 % |
| Précision (par épisode) | Part des épisodes nationaux détectés qui correspondent à une vague R1 | ≥ 80 % |
| F1 (par jour) | Moyenne harmonique précision / rappel, jour par jour, sur la période de test | à comparer à la baseline par règles |
| Erreur de datation | Écart en jours entre début (et fin) détectés et officiels | ≤ 1 jour en médiane |
| Couverture des records | Part des records R2 inclus dans un épisode ou un jour atypique | proche de 100 % |
| Taux de fausses alertes | Jours atypiques détectés dans les périodes R4 | le plus bas possible |

Un épisode détecté « correspond » à une référence si leurs dates se chevauchent avec un IoU temporel ≥ 0,5 (durée commune divisée par durée totale couverte).

### 8.3 Cohérence spatiale

- **Confirmation par les voisins :** part des stations-jours d'un épisode dont au moins un voisin à moins de 100 km est aussi atypique. Un vrai phénomène météo est rarement ponctuel.
- **Autocorrélation spatiale (indice de Moran) :** les anomalies à l'intérieur d'un épisode doivent être positivement corrélées dans l'espace.
- **Compacité :** un épisode ne doit pas « sauter » d'un bout à l'autre de la France sans stations intermédiaires ; on compte les fragments.
- **Cohérence nationale :** les jours où plus de 30 % des stations sont en épisode chaud doivent coïncider avec une anomalie ITN élevée.

### 8.4 Cohérence temporelle

- **Continuité :** pas de trou supérieur à `eps_jours` à l'intérieur d'un épisode.
- **Saisonnalité plausible :** les épisodes chauds tombent surtout de mai à septembre, les épisodes cévenols surtout à l'automne.
- **Stabilité :** on relance le regroupement avec les paramètres ± 10 % ; l'indice de Rand ajusté (ARI) entre les deux découpages doit rester élevé. Un catalogue qui change tout à la moindre modification n'est pas fiable.
- **Contrôle de tendance :** le nombre d'épisodes chauds par décennie doit augmenter, comme le constate Météo-France. C'est un contrôle de vraisemblance, pas une preuve.
- **Biais de densité :** vérifier que la hausse n'est pas due à l'augmentation de `n_obs` (refaire le calcul uniquement avec les jours `quotidien`).

## 9. Qualité, gouvernance, licences

Chaque passage de couche est protégé par des tests automatiques ; un test en échec bloque la suite du pipeline au lieu de publier un Gold faux.

| Contrôle | Où | Règle |
| --- | --- | --- |
| Complétude de l'ingestion | Bronze | Aucune réponse avec `tronque = true` sans page suivante récupérée |
| Unicité | Silver | Clé (station, date) unique |
| Bornes physiques | Silver | −50 °C ≤ tn ≤ tx ≤ 50 °C en métropole ; rr ≥ 0 |
| Complétude des séries | Silver | Station exclue des seuils si plus de 10 % de jours manquants sur 1991-2020 |
| Cohérence des jointures | Silver | Toute mesure a une station dans `dim_station` |
| Plausibilité du catalogue | Gold | Durée > 0, `date_fin` ≥ `date_debut`, `n_stations` ≥ 1 |
| Non-régression | Gold | Le nombre de vagues nationales reconstruites correspond au décompte officiel |

**Gouvernance :** un dictionnaire de données (chaque colonne Gold : définition, unité, source), le lignage Bronze → Silver → Gold documenté, les versions de tables conservées par Delta Lake, et les paramètres de détection stockés dans un fichier de configuration versionné (Git).

**Licences :** les données Météo-France sont sous Licence Ouverte Etalab 2.0, les agrégats Infoclimat sous leur politique open data. Chaque réponse de l'API porte ses champs `licence` et `attribution` : on les stocke en Bronze et on cite « Météo-France / Infoclimat » dans le dashboard et le rapport. Aucune donnée personnelle n'est traitée, donc pas d'enjeu RGPD.

## 10. Mise en œuvre et livrables

On avance en cinq étapes, chacune livre quelque chose de démontrable ; l'ordre compte, car chaque couche dépend de la précédente.

**Étape 1 — Exploration (comprendre avant de construire)**

- [ ] Tester chaque endpoint retenu dans le navigateur (liens « Tester l'API » du portail)
- [ ] Choisir 5 stations pilotes (ex. Paris-Montsouris 75114001, Mende 48043001) et tracer tx sur l'été 2003
- [ ] Lister les stations candidates via `/ref/station-parametre` et appliquer les critères de la section 3

**Étape 2 — Bronze**

- [ ] Monter Docker Compose : MinIO, Spark, Airflow
- [ ] Script d'ingestion avec pagination et retry (squelette ci-dessous)
- [ ] Ingestion complète des 7 endpoints, contrôle « aucune réponse tronquée »

**Étape 3 — Silver**

- [ ] `dim_station`, `mesures_journalieres` avec drapeaux qualité
- [ ] `seuils_climato` (P10, P90, P99) et anomalies
- [ ] Tests de qualité entre couches

**Étape 4 — Gold et IA**

- [ ] Baseline par règles : `jours_atypiques`, épisodes locaux, vagues ITN (R1)
- [ ] Isolation Forest, ST-DBSCAN, KMeans ; tables `episodes`, `records`, `indicateurs_locaux`
- [ ] Dashboard : carte des épisodes, frise temporelle, fiche station

**Étape 5 — Évaluation et dossier**

- [ ] Construire R1 à R4, calculer les métriques sur 2011 → aujourd'hui
- [ ] Comparer IA et baseline, analyser 3 épisodes en détail (dont 2003)
- [ ] Rédiger le dossier d'architecture

### Squelette d'ingestion Bronze

```python
import json, time, pathlib, datetime, requests

BASE = "https://climato.chom.engineering"

def fetch_all(dataset, params):
    """Recupere toutes les pages d'un dataset (plafond 20 000 lignes)."""
    pages = []
    while True:
        for essai in range(2):
            r = requests.get(f"{BASE}/{dataset}", params=params, timeout=15)
            if r.status_code == 503:
                time.sleep(int(r.headers.get("Retry-After", 2)))
                continue
            r.raise_for_status()
            break
        page = r.json()
        pages.append(page)
        if not page.get("tronque") or not page.get("suite"):
            return pages
        params = {**params, "from": page["suite"]["from"]}

def ingest(dataset, station, start="1991-01-01", end="2026-01-01"):
    pages = fetch_all(dataset, {"station": station, "from": start, "to": end})
    jour = datetime.date.today().isoformat()
    out = pathlib.Path(f"bronze/{dataset.replace('/', '_')}/ingest={jour}/station={station}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pages, ensure_ascii=False))

ingest("v2/journaliere", "07486")
```

### Plan du dossier d'architecture à rendre

1. Contexte et objectifs (thème, questions métier, utilisateurs)
2. Sources de données (familles, datasets retenus et écartés, contraintes d'accès, pièges)
3. Architecture cible (schéma médaillon, flux, stack et choix argumentés)
4. Modèle de données (Bronze, Silver, Gold, dictionnaire)
5. Application IA (méthodes, variables, pourquoi ces modèles)
6. Évaluation (jeu de référence, métriques, résultats, limites)
7. Qualité, gouvernance, sécurité, licences
8. Limites et perspectives (horaire, réseau StatIC, prévision, passage au bulk Iceberg)

## Journal de mise en œuvre

### Étape 0 — Contrats de données (ODCS) et test des endpoints

Avant d'ingérer quoi que ce soit, on écrit pour chaque source un contrat de données au format ODCS v3.1.0 (Open Data Contract Standard, Bitol / Linux Foundation) : ce que nous attendons de la source, et que le pipeline vérifie automatiquement. Le portail décrit lui-même ses datasets par des contrats ODCS versionnés : nous parlons le même langage que notre fournisseur.

**Pourquoi un contrat en premier :** il fixe le schéma, les unités, les clés et les règles qualité avant le code. Si la source change (colonne renommée, valeur aberrante, doublons), le test du contrat échoue et on bloque l'ingestion au lieu de publier un Gold faux.

| Section du contrat | Ce qu'on y met pour /v2/journaliere |
| --- | --- |
| Fondamentaux | id, nom, version 0.1.0, statut draft, domaine climat |
| description | but, usage (pagination), limites (statut\_extremes, plafond 20 000) |
| servers | l'API source et le dossier Bronze local où on teste |
| schema | 11 colonnes, clé (ic\_id, date), types, bornes physiques, unités |
| quality | pas de doublon (ic\_id, date), tn ≤ tx, statut\_extremes dans 3 valeurs |
| slaProperties | fraîcheur quotidienne, disponibilité J-1 |
| team, support, customProperties | responsable, doc source, licence et attribution |

Contrats à écrire, un par endpoint retenu :

- [x] `source_v2_journaliere` v0.3.0 — 27 contrôles OK
- [x] `source_baseline_1991_2020` v0.1.0 — 23 contrôles OK
- [x] `source_v2_records` v0.1.0 — 35 contrôles OK
- [x] `source_itn` v0.2.0 (ITN Météo-France, ITN v2 et normale) — 50 contrôles OK
- [x] `source_ref_stations` v0.1.0 — 38 contrôles OK
- [x] `source_records_battus` v0.1.0 — 25 contrôles OK

Étape 0 terminée : 6 contrats, 198 contrôles validés sur des échantillons réels.

Outil : `datacontract-cli` (`datacontract lint` vérifie que le fichier respecte ODCS ; `datacontract test` exécute les règles sur les données).

**Preuve que les tests servent :** une ligne en double et un `tn` de 40 °C supérieur à `tx` ont été injectés dans l'échantillon ; le test a échoué sur les deux règles (doublon et tn ≤ tx).

#### Test des endpoints — observations du 2026-10-05

| Endpoint testé | Résultat observé | Conséquence pour le projet |
| --- | --- | --- |
| `/ready` | 200, `pret: true`, 33 tables au catalogue | Service disponible |
| `/coverage` | Archive de 1777 au 2026-08-12 ; source indiquée : le fonds horaire canonique | Ne mesure pas la fraîcheur des datasets journaliers : on ne s'en sert pas comme indicateur de fraîcheur |
| `/v2/journaliere` (07486, août → octobre 2026) | 60 lignes, dernière date 2026-09-29, interrogé le 2026-10-05 | Retard réel de 6 jours (J-1 annoncé) : la fraîcheur se mesure sur max(date) reçu, alerte au-delà de 14 jours |
| `/v2/journaliere` (07486, juillet 2025) | 31 lignes, colonnes identiques au contrat ; enveloppe : station, licence, attribution, n, plafond, tronque, fenetre, suite, temps\_ms | Contrat confirmé ; licence et attribution stockées en Bronze |
| `/v2/journaliere` (07486, 1960 → 2026) | Tronqué à 20 000 lignes, du 1968-01-01 au 2022-10-07, reprise au 2022-10-08 | Pagination obligatoire ; 1991 → 2026 tient en une page par station |
| `/v2/journaliere` sans station | 400 `missing-parameter`, format RFC 9457 avec `request_id` | Le script d'ingestion branche sur le champ `code` |
| `/stations?q=trappes` | Objet `{attribution, n, q, stations[]}` | Les résultats sont dans `stations` |
| `/ref/stations` | GeoJSON `FeatureCollection`, 21 652 stations tous réseaux ; champs non documentés `reseau` et `acces` (identifiant à utiliser par famille d'API) | 1re station = poste participatif sans mfid : on filtre sur mfid et `acces.dataclimat` non nuls, France métropolitaine |
| `/ref/station-parametre` (07486) | 72 paramètres, température depuis 1941 | Profondeur des séries vérifiable station par station |
| `/dataclimat/baseline_1991_2020` (75114001) | Normales présentes, 30 années, classe 4 | Paris-Montsouris a une normale mais pas de records dataclimat |
| `/dataclimat/records_battus` | 75114001 (classe 4) : 0 ligne ; Pontarlier 25462001 (classe 1) : 445 lignes, dont un record froid de −1,5 °C le 1897-01-01 | R2 ne couvre que les classes ≤ 3 ; les records de début de série sont exclus (au moins 30 ans de mesures avant le record) |
| `/v2/records` (07486, janvier) | Record chaud 17,8 °C le 2025-01-25, record froid −25,8 °C le 1971-01-03, statut quotidien | Contrat confirmé |
| `/dataclimat/itn` vs `/v2/itn` (août 2003) | Écart de 0 à 0,43 °C ; les deux séries donnent la même vague du 2 au 17 août (max 29,38 et 29,16 °C) ; v2 passe à 0,01 °C du seuil le 15 | Référence R1 = `/dataclimat/itn` (Météo-France), v2 en contrôle croisé ; règle codée dans `vagues_chaleur_itn.py` |
| `/dataclimat/itn_baseline` | Moyenne, écart-type, P20, P80 par jour | Contrat confirmé |

## 11. Glossaire et questions probables du jury

### Glossaire

| Terme | Définition |
| --- | --- |
| Anomalie | Écart entre la valeur mesurée et la normale du lieu et du jour |
| Normale 1991-2020 | Moyenne sur 30 ans, période de référence climatique officielle actuelle |
| Percentile P90 | Valeur dépassée seulement 10 % des jours dans la période de référence |
| ITN | Indicateur Thermique National : moyenne quotidienne de 30 stations, utilisée par Météo-France pour définir les vagues de chaleur |
| ETCCDI | Groupe d'experts international qui a standardisé les indices d'extrêmes climatiques (TX90p, TN10p, R99p…) |
| Nuit tropicale | Nuit où la température minimale reste ≥ 20 °C |
| Delta Lake | Format de table sur Parquet qui ajoute transactions, versions et contrôle de schéma |
| Isolation Forest | Algorithme qui repère les anomalies en mesurant la facilité à isoler un point |
| ST-DBSCAN | Clustering par densité qui tient compte à la fois de la distance et du temps |
| IoU temporel | Durée commune de deux périodes divisée par leur durée totale cumulée |

### Questions probables et réponses courtes

- **« Pourquoi parler de Big Data si vous prenez 300 stations ? »** L'architecture est conçue pour le volume complet (145 M lignes journalières, 39 Md de mesures en amont) : Spark et Delta Lake ne changent pas, seul le périmètre d'ingestion change. Le sous-ensemble est un choix de démonstration.
- **« Pourquoi ne pas utiliser directement les données Gold du portail ? »** Elles donnent des mesures et des records, pas des épisodes. Notre valeur ajoutée est le passage mesure → anomalie → événement → épisode.
- **« Votre IA n'est-elle pas juste un seuil ? »** Le seuil est la baseline. L'IA ajoute la combinaison de variables, la comparaison aux voisins (séparer météo et panne de capteur) et le regroupement spatio-temporel, puis on mesure le gain.
- **« Comment savez-vous que vos épisodes sont vrais ? »** Jeu de référence en 4 parties, période de test séparée de la période de réglage, et contrôles de cohérence qui ne dépendent d'aucune étiquette.
- **« Et si les mesures anciennes sont moins précises ? »** On utilise `n_obs` et `statut_extremes`, et on vérifie que les tendances tiennent sur les seuls jours bien mesurés.
- **« Pourquoi Bronze en JSON brut ? »** Pour garder une copie fidèle et pouvoir tout recalculer si une règle change ou si l'API évolue.

### Sources

- [Portail Infoclimat / Météo-France — catalogue des datasets](https://portail.chom.engineering/)
- [Guide technique complet du portail (llms-full.txt)](https://portail.chom.engineering/llms-full.txt)
- [Spécification OpenAPI de l'API](https://portail.chom.engineering/openapi.json)
- [Météo-France — Canicule, vague ou pic de chaleur : critères officiels](https://meteofrance.com/actualites-et-dossiers/comprendre-la-meteo/canicule-vague-ou-pic-de-chaleur)
- [Indicateur thermique national (Wikipédia)](https://fr.wikipedia.org/wiki/Indicateur_thermique_national)
