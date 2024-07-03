from re import finditer
from bs4 import element
from lark import Lark

import pycountry

from parsers import SPEED_GRAMMAR

parser = Lark(SPEED_GRAMMAR)

class ParseError(Exception):
    pass


class TableRowHelper:
    """A simplified interface around a set of table rows from bs4.

    This abstracts all the evil stuff like rowspan and colspan so that the data
    can be reasonably parsed.
    """

    def __init__(self):
        self.td_cache = {}

    def set_tds(self, tds: [element.Tag]):
        # Nuke any existing cache entries that "expire" this round (rowspan)
        for k in list(self.td_cache.keys()):
            (remaining, value) = self.td_cache[k]
            if remaining == 1:
                del self.td_cache[k]
            else:
                self.td_cache[k] = (remaining - 1, value)

        # Add new data for this row
        col_idx = 0
        for td in tds:
            rowspan = int(td.get("rowspan", 1))

            while col_idx in self.td_cache:
                col_idx += 1  # Skip cols that are around from a prev iteration due to rowspan

            for _ in range(int(td.get("colspan", 1))):
                self.td_cache[col_idx] = (rowspan, td)
                col_idx += 1

    def get_td(self, idx) -> element.Tag:
        return self.td_cache[idx][1]


def is_uninteresting(tag: element.Tag):
    return tag.name in {"sup", "img"}


def parse_road_types_table(table) -> dict:
    result = {}
    table_row_helper = TableRowHelper()

    # Remove links (footnotes etc), images, etc. that don't serialize well.
    for junk_tag in table.find_all(is_uninteresting):
        junk_tag.decompose()

    for row in table.find_all("tr"):
        # Loop through columns
        tds = row.find_all("td")
        table_row_helper.set_tds(tds)
        if tds:
            road_type = table_row_helper.get_td(0).get_text(strip=True)
            tags_filter = table_row_helper.get_td(1).get_text(" ", strip=True)
            fuzzy_tags_filter = table_row_helper.get_td(2).get_text(" ", strip=True)
            relation_tags_filter = table_row_helper.get_td(3).get_text(" ", strip=True)
            road_class = {}
            if tags_filter: road_class['filter'] = tags_filter
            if fuzzy_tags_filter: road_class['fuzzyFilter'] = fuzzy_tags_filter
            if relation_tags_filter: road_class['relationFilter'] = relation_tags_filter
            result[road_type] = road_class

    return result


def parse_speed_table(table, speed_parse_func) -> dict:
    column_names = []
    result = {}
    warnings = []
    table_row_helper = TableRowHelper()

    # Remove links (footnotes etc), images, etc. that don't serialize well.
    for junk_tag in table.find_all(is_uninteresting):
        junk_tag.decompose()

    for row in table.find_all("tr"):
        # Handle column names
        th_tags = row.find_all("th")
        if len(th_tags) > 0:
            if len(column_names) == 0:
                for th in th_tags:
                    th_text = th.get_text(strip=True)
                    for _ in range(int(th.get("colspan", 1))):
                        column_names.append(th_text)
            else:
                for (i, th) in enumerate(th_tags):
                    th_text = th.get_text(strip=True)
                    if th_text:
                        for j in range(int(th.get("colspan", 1))):
                            column_names[i + j] = th_text

        # Loop through columns
        tds = row.find_all("td")
        table_row_helper.set_tds(tds)
        if tds:
            country = table_row_helper.get_td(0).get_text(strip=True)
            country_code = get_country_code(country)
            if not country_code:
                warnings.append(f'{country}: Unknown country / subdivision')
                continue
            
            road_type = table_row_helper.get_td(1).get_text(strip=True)

            road_tags = {}
            for col_idx in range(2, len(column_names)):
                td = table_row_helper.get_td(col_idx)
                speeds = td.get_text(strip=True)

                if speeds:
                    vehicle_type = column_names[col_idx]
                    try:
                        parsed_speeds = speed_parse_func(speeds)
                    except Exception:
                        parsed_speeds = {}
                        warnings.append(f'{country_and_subdivision_name}: Unable to parse \'{vehicle_type}\' for \'{road_type}\'')

                    for key, value in parsed_speeds.items():
                        if vehicle_type != "(default)":
                            key = key.replace("maxspeed", "maxspeed:" + vehicle_type, 1)
                            key = key.replace("access", vehicle_type)
                        road_tags[key] = value

            if country_code not in result:
                result[country_code] = []

            road_class = { 'tags': road_tags }
            if road_type:
                road_class['name'] = road_type
                
            result[country_code].append(road_class)

    return {'speedLimitsByCountryCode': result, 'warnings': warnings}
    

def get_country_code(name):
    if name in country_codes:
        return country_codes[name]

    country_and_subdivision_name = name.split(":")
    country_name = country_and_subdivision_name[0].strip()
    try:
        country_code = pycountry.countries.lookup(country_name).alpha_2
        
        if len(country_and_subdivision_name) > 1:
            subdivision_name = country_and_subdivision_name[1].strip()
            subdivisions = pycountry.subdivisions.get(country_code=country_code)
            for subdivision in subdivisions:
                if subdivision.name == subdivision_name:
                    return subdivision.code
            return
        else:
            return country_code
        
    except Exception:
        return

country_codes = {
    "Brunei": "BN",
    "Belgium:Brussels-Capital Region": "BE-BRU",
    "Belgium:Flanders": "BE-VLG",
    "Belgium:Wallonia": "BE-WAL",
    "Democratic Republic of the Congo": "CD",
    "Kosovo": "XK",
    "Micronesia": "FM",
    "Micronesia:Kosrae": "FM-KSA",
    "Micronesia:Pohnpei": "FM-PNI",
    "Micronesia:Chuuk": "FM-TRK",
    "Micronesia:Yap": "FM-YAP",
    "Netherlands:Bonaire": "NL-BQ1",
    "Netherlands:Saba": "NL-BQ2",
    "Netherlands:Sint Eustatius": "NL-BQ3",
    "Palestine": "PS",
    "Pitcairn Islands": "PN",
    "Russia": "RU",
    "Turkey": "TR",
    "United Kingdom:Scotland": "GB-SCT"
}


def validate_road_types(road_types: dict):
    warnings = []
    for road_type, filters in road_types.items():
        all_filters = []
        if "filter" in filters: all_filters.append(filters["filter"])
        if "fuzzyFilter" in filters: all_filters.append(filters["fuzzyFilter"])
        if "relationFilter" in filters: all_filters.append(filters["relationFilter"])
        for f in all_filters:
            for match in finditer("{.*?}", f):
                placeholder = match.group(0)[1:-1]
                if placeholder not in road_types:
                    warnings.append(f'{road_type}: Unable to map \'{placeholder}\'')
    return warnings

def validate_road_types_in_speed_table(speeds_by_country_code: dict, road_types: dict):
    warnings = []
    for country_code in speeds_by_country_code:
        for road_class in speeds_by_country_code[country_code]:
            if "name" in road_class:
                road_type = road_class["name"]
                if road_type not in road_types:
                    warnings.append(f'{country_code}: Unable to map \'{road_type}\'')
    return warnings
