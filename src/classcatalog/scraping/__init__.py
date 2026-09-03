"""Typed SDSU schedule scraping, parsing, fixture capture, and synchronization adapters."""

from classcatalog.scraping.models import CourseSearchHit, SubjectScrapeResult
from classcatalog.scraping.session import SdsuPeopleSoftSession

__all__ = ["CourseSearchHit", "SdsuPeopleSoftSession", "SubjectScrapeResult"]
