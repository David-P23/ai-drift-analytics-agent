import os
from dotenv import load_dotenv
from datetime import datetime
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_PATH = os.path.join(os.path.dirname(__file__), "../data/processed/northstar_drift.db")

# --------------------------------
# Company Info
# --------------------------------

COMPANY_NAME = "NorthStar Financial"
EMAIL_DOMAIN = "northstarfinancial.com"


# --------------------------------
# Line of Business
# --------------------------------

LOB_LIST = [
    "Technology Resiliency",
    "Payments",
    "Customer Platforms",
    "Risk & Compliance",
    "Treasury & Finance",
    "Internal Services"
]


# --------------------------------
# Owner Teams
# --------------------------------

OWNER_TEAMS = [
    "Core Infrastructure",
    "Application Support",
    "Platform Engineering",
    "Middleware Services",
    "Database Engineering",
    "Enterprise Monitoring",
    "Cybersecurity Operations",
    "Digital Platforms",
    "Treasury Technology",
    "Risk Technology",
    "Payments Engineering",
    "Resiliency Engineering"
]


# --------------------------------
# Datacenters
# --------------------------------

DATACENTERS = [
    "Eagan",
    "Chandler",
    "Phoenix",
    "Des Moines",
    "Charlotte",
    "Dallas",
    "Ashburn",
    "Columbus",
    "Minneapolis",
    "St. Louis"
]


# --------------------------------
# Environments
# --------------------------------

ENVIRONMENTS = [
    "Production",
    "Pre-Production",
    "QA",
    "Dev"
]


# --------------------------------
# Hosting Types
# --------------------------------

HOSTING_TYPES = [
    "On-Prem",
    "Hybrid Cloud",
    "Private Cloud",
    "Public Cloud"
]


# --------------------------------
# OS Families
# --------------------------------

OS_FAMILIES = [
    "Linux",
    "Windows",
    "Unix",
    "Mixed"
]


# --------------------------------
# App Status
# --------------------------------

APP_STATUS = [
    "Active",
    "Retiring",
    "Decommission Planned",
    "In Development",
    "Archived"
]


# --------------------------------
# Deployment Status
# --------------------------------

DEPLOY_STATUS = [
    "Production",
    "Hybrid",
    "Non-Production",
    "Decommissioning"
]


# --------------------------------
# Product Categories
# --------------------------------

PRODUCT_CATEGORIES = [
    "Operating System",
    "Database",
    "Middleware",
    "Web Server / API",
    "Security / Identity",
    "Messaging / Integration",
    "Monitoring / Agent"
]


# --------------------------------
# Root Causes
# --------------------------------

ROOT_CAUSES = [
    "Patch not tested",
    "Vendor compatibility issue",
    "Upgrade window unavailable",
    "Application dependency blocker",
    "Infrastructure constraint",
    "Planned decommission",
    "False positive detection"
]


# --------------------------------
# RTO Score Mapping
# --------------------------------

RTO_MAP = {
    1: 0.5,
    2: 1,
    3: 2,
    4: 4,
    5: 8,
    6: 12,
    7: 24,
    8: 48,
    9: 72,
    10: 120
}


# --------------------------------
# Note Types
# --------------------------------

NOTE_TYPES = [
    "Exemption Request",
    "Exemption Review",
    "Owner Update",
    "Remediation Update",
    "Escalation",
    "Validation Result",
    "Closure Note"
]


# --------------------------------
# Author Roles
# --------------------------------

AUTHOR_ROLES = [
    "Application Owner",
    "Resiliency Lead",
    "Compliance Analyst",
    "Infrastructure Engineer",
    "Platform Engineer"


]