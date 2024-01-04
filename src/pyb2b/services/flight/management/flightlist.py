class ParseFields:
    def __init__(self) -> None:
        self.route: None | str = None

    def parse(self, elt: ElementTree.Element) -> dict[str, Any]:
        if ("Time" in elt.tag or "time" in elt.tag) and elt.text is not None:
            return {elt.tag: pd.Timestamp(elt.text, tz="utc")}
        if elt.text is not None:
            return {elt.tag: elt.text}
        method: Callable[..., dict[str, Any]] = getattr(  # type: ignore
            type(self), elt.tag, None
        )
        if method is None:
            self.unknown(elt)
        return method(self, elt)

    def unknown(self, elt: ElementTree.Element) -> NoReturn:
        s = ElementTree.tostring(elt)
        raise Exception(minidom.parseString(s).toprettyxml(indent="  "))

    def flightLevel(self, point: ElementTree.Element) -> dict[str, Any]:
        level = point.find("level")
        unit = point.find("unit")
        if level is not None and unit is not None:
            if unit.text == "F" and level.text is not None:
                return {"altitude": 100 * int(level.text)}

        self.unknown(point)

    def associatedRouteOrTerminalProcedure(
        self, point: ElementTree.Element
    ) -> dict[str, Any]:
        sid = point.find("SID")
        star = point.find("STAR")
        route = point.find("route")
        if sid is not None:
            self.route = None
            id_ = sid.find("id")
            aerodrome = sid.find("aerodromeId")
            return {
                "route": id_.text if id_ is not None else None,
                "aerodrome": aerodrome.text if aerodrome is not None else None,
            }
        elif star is not None:
            self.route = None
            id_ = star.find("id")
            aerodrome = star.find("aerodromeId")
            return {
                "route": id_.text if id_ is not None else None,
                "aerodrome": aerodrome.text if aerodrome is not None else None,
            }
        elif route is not None:
            self.route = route.text
            return {"route": route.text}
        elif point.find("DCT") is not None:
            return {"route": "DCT"}

        self.unknown(point)

    def point(self, point: ElementTree.Element) -> dict[str, Any]:
        pointId = point.find("pointId")
        if pointId is not None:
            from traffic.data import airways, navaids

            rep: dict[str, Any] = {"FIX": pointId.text}
            if self.route is not None:
                airway = airways[self.route]
                if airway is not None:
                    nx = navaids.extent(airway)
                    if nx is not None:
                        fix = nx[pointId.text]
                        if fix is not None:
                            rep["latitude"] = fix.latitude
                            rep["longitude"] = fix.longitude
            return rep
        dbePoint = point.find("nonPublishedPoint-DBEPoint")
        if dbePoint is not None:
            return {"FIX": dbePoint.text}
        geopoint = point.find("nonPublishedPoint-GeoPoint")
        if geopoint is not None:
            angle = geopoint.find("position/latitude/angle")
            side = geopoint.find("position/latitude/side")
            assert angle is not None and side is not None
            lat = int(angle.text) / 10000  # type: ignore
            if side.text == "SOUTH":
                lat *= -1

            angle = geopoint.find("position/longitude/angle")
            side = geopoint.find("position/longitude/side")
            assert angle is not None and side is not None
            lon = int(angle.text) / 10000  # type: ignore
            if side.text == "WEST":
                lat *= -1

            return {"latitude": lat, "longitude": lon}

        return self.unknown(point)


class FlightInfo:
    @classmethod
    def from_file(cls, filename: str) -> "FlightInfo":
        et = ElementTree.parse(filename)
        return cls.fromET(et.getroot())

    def to_xml(self, filename: None | str | Path = None) -> None:
        if isinstance(filename, str):
            filepath = Path(filename)

        if isinstance(filename, Path):
            filepath = filename

        if filename is None or filepath.is_dir():
            name = "{eobt:%Y%m%d}_{id_}_{callsign}_{from_}_{to}.xml"
        elif isinstance(filename, str):
            name = filename

        name = name.format(
            id_=self.flight_id,
            eobt=self.estimatedOffBlockTime,
            callsign=self.callsign,
            from_=self.aerodromeOfDeparture,
            to=self.aerodromeOfDestination,
        )

        if filepath.is_dir():
            filepath = filepath / name
        else:
            filepath = Path(name)

        ElementTree.ElementTree(self.reply).write(filepath)

    @property
    def flight_id(self) -> str:
        assert self.reply is not None
        elt = self.reply.find("flightId/id")
        assert elt is not None
        assert elt.text is not None
        return elt.text

    @property
    def flight_plan(self) -> Any:
        """Returns the flight plan in ICAO format."""
        from traffic.core import FlightPlan

        return FlightPlan(
            self.icaoRoute,
            self.aerodromeOfDeparture,
            self.aerodromeOfDestination,
        )

    @property
    def callsign(self) -> None | str:
        if hasattr(self, "aircraftId"):
            return self.aircraftId
        return None

    @property
    def icao24(self) -> None | str:
        if hasattr(self, "aircraftAddress"):
            return self.aircraftAddress.lower()
        return None

    def _repr_html_(self) -> str:
        from traffic.core.mixins import _HBox
        from traffic.data import aircraft, airports

        aircraft_fmt = "<code>%icao24</code> · %flag %registration (%typecode)"
        title = f"<b>Flight {self.flight_id}</b> "
        title += "<ul>"
        if hasattr(self, "aircraftId"):
            title += f"<li><b>callsign:</b> {self.aircraftId}</li>"
        departure = airports[self.aerodromeOfDeparture]
        destination = airports[self.aerodromeOfDestination]
        title += f"<li><b>from:</b> {departure:%name (%icao/%iata)}</li>"
        title += f"<li><b>to:</b> {destination:%name (%icao/%iata)}</li>"
        if hasattr(self, "aircraftAddress"):
            ac = aircraft.get(self.aircraftAddress.lower())
            title += "<li><b>aircraft:</b> {aircraft}</li>".format(
                aircraft=format(ac, aircraft_fmt)
            )

        cumul = list()
        cumul.append(
            pd.DataFrame.from_dict(
                [
                    dict(
                        (value, getattr(self, key, None))
                        for key, value in rename_cols.items()
                        if len(value) == 4
                    )
                ]
            ).T.rename(columns={0: self.flight_id})
        )

        no_wrap_div = '<div style="float: left; margin: 10px">{}</div>'
        fp = self.flight_plan
        fp_svg = fp._repr_svg_()
        return (
            title
            + "<br/><code>"
            + "<br/>".join(textwrap.wrap(re.sub(r"\s+", " ", fp.repr).strip()))
            + "</code><br/>"
            + (no_wrap_div.format(fp_svg) if fp_svg is not None else "")
            + cast(str, _HBox(*cumul)._repr_html_())
        )

    def __getattr__(self, name: str) -> str | pd.Timestamp:
        cls = type(self)
        assert self.reply is not None
        elt = self.reply.find(name)
        if elt is None:
            elt = self.reply.find("flightId/keys/" + name)
        if elt is not None and elt.text is not None:
            if "Time" in name or "time" in name:
                return pd.Timestamp(elt.text, tz="utc")
            return elt.text
        msg = "{.__name__!r} object has no attribute {!r}"
        raise AttributeError(msg.format(cls, name))

    def parsePlan(self, name: str) -> None | pd.DataFrame:
        """
        If available, parse the FTFM (m1), RTFM (m2) or CTFM (m3) profiles.

        :param name: one of ftfmPointProfile, rtfmPointProfile, ctfmPointProfile

        """
        assert self.reply is not None
        msg = "No {} found in requested fields"
        if self.reply.find(name) is None:
            warnings.warn(msg.format(name))
            return None
        parser = ParseFields()
        return (
            pd.DataFrame.from_records(
                [
                    dict(elt for p in point for elt in parser.parse(p).items())
                    for point in self.reply.findall(name)
                ]
            )
            .rename(columns={"timeOver": "timestamp"})
            .assign(
                flightPlanPoint=lambda x: x.flightPlanPoint == "true",
                icao24=self.aircraftAddress.lower()
                if hasattr(self, "aircraftAddress")
                else None,
                callsign=self.aircraftId,
                origin=self.aerodromeOfDeparture,
                destination=self.aerodromeOfDestination,
                flight_id=self.flight_id,
                EOBT=self.estimatedOffBlockTime,
                typecode=self.aircraftType,
            )
        )


# https://github.com/python/mypy/issues/2511
FlightListTypeVar = TypeVar("FlightListTypeVar", bound="FlightList")


class FlightList(DataFrameMixin):
    columns_options = dict(
        flightId=dict(style="blue bold"),
        callsign=dict(),
        icao24=dict(),
        typecode=dict(),
        origin=dict(),
        destination=dict(),
        EOBT=dict(),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if len(args) == 0 and "data" not in kwargs:
            super().__init__(data=None, **kwargs)
        else:
            super().__init__(*args, **kwargs)

    @classmethod
    def fromB2BReply(cls: Type[FlightListTypeVar], r) -> FlightListTypeVar:
        assert r.reply is not None
        return cls.fromET(r.reply)

    @classmethod
    def fromET(
        cls: Type[FlightListTypeVar], tree: ElementTree.Element
    ) -> FlightListTypeVar:
        instance = cls()
        instance.reply = tree
        instance.build_df()
        return instance

    def __getitem__(self, item: str) -> None | FlightInfo:
        assert self.reply is not None
        for elt in self.reply.findall("data/flights/flight"):
            key = elt.find("flightId/id")
            assert key is not None
            if key.text == item:
                return FlightInfo.fromET(elt)

        return None

    def _ipython_key_completions_(self) -> set[str]:
        return set(self.data.flightId.unique())

    def build_df(self) -> None:
        assert self.reply is not None

        self.data = pd.DataFrame.from_records(
            [
                {
                    **{
                        "flightId": elt.find("flightId/id").text  # type: ignore
                        if elt.find("flightId/id") is not None
                        else None
                    },
                    **{
                        p.tag: p.text
                        for p in elt.find("flightId/keys")  # type: ignore
                    },
                    **{
                        p.tag: p.text
                        for p in elt
                        if p.tag != "flightId" and p.text is not None
                    },
                }
                for elt in self.reply.findall("data/flights/flight")
            ]
        )

        self.format_data()

    def format_data(self) -> None:
        if "nonICAOAerodromeOfDeparture" in self.data.columns:
            self.data = self.data.drop(
                columns=[
                    "nonICAOAerodromeOfDeparture",
                    "nonICAOAerodromeOfDestination",
                    "airFiled",
                ]
            )

        self.data = self.data.rename(columns=rename_cols).replace(
            "SLOT_TIME_NOT_LIMITED", ""
        )

        for feat in [
            "AOBT",
            "ATOA",
            "ATOT",
            "COBT",
            "CTOA",
            "CTOT",
            "EOBT",
            "ETOA",
            "ETOT",
        ]:
            if feat in self.data.columns:
                self.data = self.data.assign(
                    **{
                        feat: self.data[feat].apply(
                            lambda x: pd.Timestamp(x, tz="utc")
                        )
                    }
                )

        for feat in ["currentlyUsedTaxiTime", "taxiTime", "delay"]:
            if feat in self.data.columns:
                self.data = self.data.assign(
                    **{
                        feat: self.data[feat].apply(
                            lambda x: pd.Timedelta(
                                f"{x[:2]} hours {x[2:4]} minutes "
                                + f"{x[4:6]} seconds"
                                if feat == "currentlyUsedTaxiTime"
                                else ""
                            )
                            if x == x
                            else pd.Timedelta("0")
                        )
                    }
                )

        if "icao24" in self.data.columns:
            self.data = self.data.assign(icao24=self.data.icao24.str.lower())

        if "EOBT" in self.data.columns:
            self.data = self.data.sort_values("EOBT")
