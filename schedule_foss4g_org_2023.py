#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import os
from typing import List
import requests
import json
import pytz
import sys
import optparse
from datetime import datetime

from voc import (
    GenericConference,
    PretalxConference,
    WebcalConference,
    Event,
    Schedule,
    ScheduleEncoder,
    ScheduleException,
    Room,
    Logger,
)

from voc.c3data import C3data
from git import Repo

# from voc.schedule import set_validator_filter
from voc.tools import (
    commit_changes_if_something_relevant_changed,
    git,
    harmonize_event_type,
    write,
    ensure_folders_exist,
)

tz = pytz.timezone("Europe/Amsterdam")
local = False

parser = optparse.OptionParser()
parser.add_option("--online", action="store_true", dest="online", default=False)
parser.add_option(
    "--fail", action="store_true", dest="exit_when_exception_occours", default=local
)
parser.add_option("--stats", action="store_true", dest="only_stats", default=False)
parser.add_option("--git", action="store_true", dest="git", default=False)
parser.add_option("--debug", action="store_true", dest="debug", default=local)

log = Logger(__name__)
options, args = parser.parse_args()

xc3 = "jev22"

# source pad https://lab.nrw/hedgedoc/_hV5kXt9TKuiV0DVsdIwgg
conferences: List[GenericConference] = [
]

targets = [
    "filesystem",
    "c3data",
    # "voctoimport",
    # "rc3hub"
]

id_offsets = {
    #   when 10 additional speakers are created there
    # c3voc preatax schedule local ids's range from 120 to till >500
}


# this list/map is required to sort the events in the schedule.xml in the correct way
# other rooms/assemblies are added at the end on demand.
rooms = {
    "channels": [
        # channels with video recordings/livestream – same order as streaming website
    ],
    "rooms": [],
    "music": [],
}

channels = {}
for c in rooms['channels']:
    channels[c.guid or c.name] = c

output_dir = "/srv/www/" + xc3
secondary_output_dir = "./" + xc3
if len(sys.argv) == 2:
    output_dir = sys.argv[1]

local = ensure_folders_exist(output_dir, secondary_output_dir)


def main():
    base_schedule = Schedule(
        conference={
            "acronym": "jev22",
            "title": "Dezentrale Jahresendveranstaltungen",
            "start": "2022-12-26",
            "end": "2022-12-31",
            "daysCount": 6,
            "timeslot_duration": "00:15",
            "time_zone_name": "Europe/Amsterdam",
        },
        version=str(datetime.now().strftime("%Y-%m-%d %H:%M")),
    )

    full_schedule = base_schedule.copy()

    # add addional rooms from this local config now, so they are in the correct order
    # for key in rooms:
    #     full_schedule.add_rooms(rooms[key])

    # add events to full_schedule
    for entry in conferences:
        try:
            print(f"\n== Conference {entry['name']} ({entry['location']})")
            schedule = entry.schedule(base_schedule)

            if schedule.get('version'):
                full_schedule['version'] += f"; {entry['name']}"
            else:
                log.warning(f'  WARNING: schedule of "{entry}" does not have a version number')

            try:
                print(f"  from {schedule['conference']['start']} to {schedule['conference']['end']}")
                print( "  contains {events_count} events, with local ids from {min_id} to {max_id}".format(**schedule.stats.__dict__))  # noqa
                print( "    local person ids from {person_min_id} to {person_max_id}".format(**schedule.stats.__dict__)) # noqa
                print(f"    rooms: {', '.join(schedule.rooms())}")

            except Exception:
                pass

            if full_schedule.add_events_from(
                schedule,
                id_offset=entry.get("id_offset") or id_offsets.get(entry["name"]) or 0,
                options={
                    "randomize_small_ids": True,
                    "overwrite_slug": True,
                    "remove_title_additions": True,
                    **(entry.options or {}),
                    "prefix_person_ids": entry.get("prefix"),
                },
                context=entry,
            ):
                print("  success")

        except ScheduleException as e:
            print(e)

        except KeyboardInterrupt:
            exit()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print("  not yet available (404)")
            else:
                print("  HTTP ERROR: " + str(e))
                if options.exit_when_exception_occours:
                    raise e
        except Exception as e:
            log.error(f"  UNEXPECTED ERROR: {type(e).__name__}: {sys.exc_info()[1]}")
            if options.exit_when_exception_occours:
                raise e

    if options.only_stats:
        exit()

    full_schedule.foreach_event(harmonize_event_type, options)

    # write all events from the channels to a own schedule.json/xml
    #  export_stages_schedule(full_schedule)
    #  export_streams_schedule(full_schedule)

    # to get proper a state, we first have to remove all event files from the previous run
    if not local or options.git:
        git("rm events/*  >/dev/null")
    os.makedirs('events', exist_ok=True)

    # write seperate file for each event, to get better git diffs
    # TODO: use Event.export()
    def export_event(event: Event):
        origin_system = None
        if isinstance(event, Event):
            origin_system = event.origin.origin_system

        with open("events/{}.json".format(event["guid"]), "w") as fp:
            json.dump(
                {
                    **event,
                    "room_id": full_schedule._room_ids.get(event["room"], None),
                    "origin": origin_system or None,
                },
                fp,
                indent=2,
                cls=ScheduleEncoder,
            )

    full_schedule.foreach_event(export_event)

    # remove overlapping 'Lötworkshop mit Lötchallenge' 
    full_schedule.remove_event(guid='bd75d959-dad1-43b4-81fb-33dfb43c10ec')

    # set_validator_filter(["precomputed", "fire-shonks-2022", "hip-berlin-2022"])
    # write all events to one big schedule.json/xml
    write("\nExporting... ")
    # set_validator_filter('strange')
    full_schedule.export("everything")

    streams_schedule = full_schedule.filter("channels", rooms=rooms['channels'])
    replacements = {}
    for stream in rooms['channels']:
        r = streams_schedule.room(guid=stream.guid)
        if r and stream.name and stream.name != r['name']:
            replacements[r['name']] = stream.name
    # streams_schedule.rename_rooms(replacements)
    streams_schedule.export("channels")

    # expose metadata to own file
    with open("meta.json", "w") as fp:
        json.dump(
            {
                "data": {
                    "version": full_schedule.version(),
                    # 'source_urls': list(loaded_schedules.keys()),
                    "rooms": [
                        {
                            **room,
                            "schedule_name": room['name'],
                            "stream": channels.get(room.get('guid', room['name']), Room()).stream
                        }
                        for room in full_schedule.rooms(mode='v2')
                    ],
                    "conferences": conferences,
                },
            },
            fp,
            indent=2,
            cls=ScheduleEncoder,
        )

    print("\nDone")
    print("  version: " + full_schedule.version())

    if options.debug:
        print("\n  rooms: ")
        for room in full_schedule.rooms():
            print("   - " + room)
        print()

    if not local or options.git:
        commit_changes_if_something_relevant_changed(full_schedule)
        # Attention: This method exits the script, if nothing relevant changed
        # TOOD: make this fact more obvious or refactor code

    if not local and "c3data" in targets:
        print("\n== Updating c3data via API…")

        c3data = C3data(full_schedule)
        c3data.process_changed_events(Repo('.'), options)


if __name__ == "__main__":
    main()
