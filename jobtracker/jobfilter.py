# -*- coding: utf-8 -*-
"""Shared "architecture = buildings" job filter.

One place for the rule so the scraper (insert time), the site exporter and
the notifier all agree. The point is to keep *building* architecture and
drop the many look-alike "architect" roles from other domains — software,
IT, business, information, landscape/garden, etc.

The domain word must sit right next to "architect", so a genuine building
title like "Architekt/in Qualitatssicherung Hochbau" is never caught — only
titles where the architecture is qualified by another field.
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
    r"web|java|\.net|ml|ai|rf|hardware|embedded|firmware|business|"
    r"process|prozess|product|produkt|brand|marketing|sales|content|"
    r"workflow|infrastructure|infrastruktur|domain|api|master\s*data|"
    r"landscape|landschaft|garten|game|gaming|ux|ui|service|quality|"
    r"test|release|delivery|functional|salesforce|oracle|dynamics|"
    r"servicenow|crm|erp|saas|paas|mulesoft|snowflake|databricks)"
    r"s?[-\s/]*(?:architekt\w*|architect\w*)"
    r"|(?:architekt|architect)\w*\s+(?:\.net|java|cloud|sap|azure|aws|iot|"
    r"software|data|systems?|security|network)\b",
    re.I)


def is_non_building(title):
    """True when the title is a non-building 'architect' (software, IT,
    business, landscape, ...). Used to drop it from the DB, site and email."""
    return bool(NON_BUILDING_ARCHITECT.search(title or ""))
