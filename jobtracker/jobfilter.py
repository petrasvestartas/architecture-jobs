# -*- coding: utf-8 -*-
"""Shared "architecture = buildings" job filter.

One place for the rule so the scraper (insert time), the site exporter and
the notifier all agree. The point is to keep *building* architecture and
drop the many look-alike "architect" roles from other domains — software,
IT, business, information, landscape/garden, etc. — plus building-trade
ads (Tiefbau, Elektro, HLK, ...) that name no architecture role.

Three tiers:
1. NON_BUILDING_ARCHITECT — a domain word right next to "architect"
   (Softwarearchitekt, Solution Architect). Adjacency keeps a genuine
   building title like "Architekt/in Qualitatssicherung Hochbau" safe.
2. HARD_IT — unambiguous IT vocabulary anywhere in a title that contains
   an architect word ("Senior Network & Automation Architect (Private
   Cloud)" — the domain words are not adjacent, but no building architect
   ad ever says cloud/network/devops). Soft words (business, digital,
   service, data, ...) stay adjacency-only: they do appear in real ads.
3. NON_ARCH_TRADE — civil/underground and building-services trades
   (Tiefbau, Elektro, Sanitar, HLK, ...) with no architecture word in the
   title. "Bauleiter Hochbau/Tiefbau" survives via "hochbau".
"""
import re

ARCH_CORE = re.compile(r"architekt|architect|architecte|architetto", re.I)

# Non-building "architect": a domain word glued or adjacent to architect
# (German compounds glue with an optional linking "s" -> the `s?`). English
# and German variants both covered.
NON_BUILDING_ARCHITECT = re.compile(
    r"\b(?:it|ict|tech|technical|technolog\w*|software|cloud|data|daten|"
    r"solutions?|l[oö]sungs?|systems?|network|netzwerk|security|sicherheits?|"
    r"enterprise|unternehmens?|application|applikations?|integration|"
    r"plat(?:t)?form|informatik|information|digital|sap|azure|aws|devops|bi|"
    r"web|java|\.net|ml|ai|ki|rf|hardware|embedded|firmware|business|"
    r"process|prozess|product|produkt|brand|marketing|sales|content|"
    r"workflow|infrastructure|infrastruktur|domain|api|master\s*data|"
    r"automations?|automatisierungs?|telecom\w*|telekom\w*|iot|cyber\w*|"
    r"identity|machine\s*learning|big\s*data|analytics|kubernetes|"
    r"mainframe|storage|virtualisierungs?|virtualization|hosting|"
    r"database|datenbank|payment|banking|versicherungs?|insurance|"
    r"finance|finanz|hr|transformations?|governance|supply\s*chain|"
    r"logistics|logistik|pricing|e-?commerce|collaboration|workplace|"
    r"observability|blockchain|"
    r"landscape|landschaft|garten|game|gaming|ux|ui|service|quality|"
    r"test|release|delivery|functional|salesforce|oracle|dynamics|"
    r"servicenow|crm|erp|saas|paas|mulesoft|snowflake|databricks)"
    r"s?[-\s/]*(?:architekt\w*|architect\w*)"
    r"|(?:architekt|architect)\w*\s+(?:\.net|java|cloud|sap|azure|aws|iot|"
    r"software|data|systems?|security|network)\b",
    re.I)

# Unambiguous IT vocabulary: anywhere in an architect title = not a
# building job, however far from the word "architect" it sits. Only words
# that never occur in genuine building-architecture ads belong here —
# soft/ambiguous ones (it, digital, data, service, sicherheit, ...) must
# stay adjacency-only above.
HARD_IT = re.compile(
    r"\b(?:cloud|software|devops|kubernetes|docker|openshift|terraform|"
    r"networks?|networking|netzwerk\w*|informatik|ict|cyber\w*|sap|azure|"
    r"aws|java|python|golang|mainframe|telecoms?|telekommunikation|"
    r"firmware|microservices?|machine\s+learning|k[uü]nstliche\s+intelligenz|"
    r"blockchain|automations?|automatisierung|salesforce|servicenow|"
    r"snowflake|databricks|mulesoft|erp|crm|datenbank\w*|databases?|"
    r"linux|sharepoint|m365|virtualisierung|virtualization|hyperscaler|"
    r"observability|security|sre|iam|pki)\b"
    r"|\.net\b|\bc#",
    re.I)

# Building trades and civil engineering: not architecture unless the title
# itself also names an architecture role. Compounds self-protect the other
# way (Heizungssanierung etc. never match because of the trailing \b).
NON_ARCH_TRADE = re.compile(
    r"\b(?:tiefbau\w*|strassenbau|gleisbau|leitungsbau|wasserbau|"
    r"elektro\w*|elektriker\w*|geb[aä]udetechnik|geb[aä]udeautomation|"
    r"hlkk?s?e?|sanit[aä]r\w*|heizung|l[uü]ftung|k[aä]lte\w*|klima\w*|"
    r"spengler\w*|solarteur\w*|photovoltaik|facility|"
    r"g[eé]nie\s+civil|[eé]lectricit[eé]|[eé]lectricien\w*|chauffage|"
    r"ventilation|climatisation|sanitaire\w*)\b",
    re.I)
BUILDING_WORD = re.compile(
    r"architekt|architect|architecte|architetto|hochbau|innenarchitekt|"
    r"st[aä]dtebau|denkmal|baukultur|raumplan|urbanis|"
    r"hoch-\s*(?:und|&|u\.|/)?\s*tiefbau", re.I)  # "Hoch- und Tiefbau" elision


def is_non_building(title):
    """True when the title is not building architecture: a non-building
    'architect' (software, IT, business, landscape, ...) or a trade ad
    (Tiefbau, Elektro, HLK, ...) naming no architecture role. Used to drop
    it from the DB, site and email."""
    t = title or ""
    if NON_BUILDING_ARCHITECT.search(t):
        return True
    if ARCH_CORE.search(t) and HARD_IT.search(t):
        return True
    if NON_ARCH_TRADE.search(t) and not BUILDING_WORD.search(t):
        return True
    return False
