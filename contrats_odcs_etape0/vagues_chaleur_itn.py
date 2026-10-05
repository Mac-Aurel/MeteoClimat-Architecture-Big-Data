"""Reconstruit les vagues de chaleur nationales à partir de l'ITN (règle Météo-France).

Règle : une vague contient au moins 1 jour avec ITN >= 25,3 °C et au moins 3 jours
avec ITN >= 23,4 °C. Elle s'arrête si l'ITN passe sous 23,4 °C deux jours de suite,
ou sous 22,4 °C un seul jour.
Sert à construire le jeu de référence R1 de l'évaluation.

Usage : python vagues_chaleur_itn.py bronze/dataclimat_itn/*.json
"""
import json
import sys

PIC, SEUIL, PLANCHER = 25.3, 23.4, 22.4


def vagues(serie):
    """serie : liste de (date, itn) triée par date. Renvoie [(debut, fin, max_itn)]."""
    resultats, en_cours, sous_seuil = [], [], 0
    for date, itn in serie + [(None, -99.0)]:  # sentinelle pour clore la dernière vague
        if itn >= SEUIL:
            en_cours.append((date, itn))
            sous_seuil = 0
            continue
        if en_cours and itn >= PLANCHER and sous_seuil == 0:
            sous_seuil = 1              # 1er jour sous 23,4 : on attend le lendemain
            continue
        # fin d'épisode (2e jour sous 23,4, ou passage sous 22,4)
        valeurs = [v for _, v in en_cours]
        if len(valeurs) >= 3 and max(valeurs) >= PIC:
            resultats.append((en_cours[0][0], en_cours[-1][0], max(valeurs)))
        en_cours, sous_seuil = [], 0
    return resultats


if __name__ == "__main__":
    lignes = []
    for chemin in sys.argv[1:]:
        lignes += json.load(open(chemin, encoding="utf-8"))
    serie = sorted((l["date"], l["itn"]) for l in lignes
                   if l.get("itn") is not None and not l.get("is_fictive"))
    for debut, fin, maxi in vagues(serie):
        print(f"vague de chaleur du {debut} au {fin}, ITN max {maxi:.2f} °C")
