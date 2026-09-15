function useLiveLocation() {

    if (!navigator.geolocation) {
        alert("Geolocation is not supported by your browser");
        return;
    }

    navigator.geolocation.getCurrentPosition(
        (position) => {

            const lat = position.coords.latitude;
            const lon = position.coords.longitude;

            console.log("Live Location:", lat, lon);

            // Call backend with coordinates instead of place
            let requiredKLD = document.getElementById("kldInput").value || 0;
            let quality = document.getElementById("qualityInput").value;
            let type = document.getElementById("typeInput").value;

            fetch(`/api/search_place?lat=${lat}&lon=${lon}&required_kld=${requiredKLD}&quality=${quality}&type=${type}`)
            .then(res => res.json())
            .then(data => {

                if (data.error) {
                    alert(data.error);
                    return;
                }

                markersLayer.clearLayers();

                let location = data.searched_location;
                let nearest = data.nearest_stp;

                // User marker
                L.marker([lat, lon])
                .addTo(markersLayer)
                .bindPopup("📍 Your Live Location")
                .openPopup();

                map.setView([lat, lon], 14);

                if (nearest) {

                    // STP marker
                    L.marker([nearest.latitude, nearest.longitude])
                    .addTo(markersLayer)
                    .bindPopup(`<b>${nearest.stp_name}</b>`);

                    // Draw route
                    fetch(`https://router.project-osrm.org/route/v1/driving/${lon},${lat};${nearest.longitude},${nearest.latitude}?overview=full&geometries=geojson`)
                    .then(res => res.json())
                    .then(routeData => {

                        const route = routeData.routes[0];

                        const routeLayer = L.geoJSON(route.geometry, {
                            style: { color: "#2563eb", weight: 5 }
                        }).addTo(markersLayer);

                        map.fitBounds(routeLayer.getBounds(), {
                            padding: [50, 50]
                        });

                        const travelTime = (route.duration / 60).toFixed(1);

                        document.getElementById("distanceStp").innerText =
                            nearest.distance_km + " km (road)";

                        document.getElementById("predictedKld").innerText =
                            "Live Location | ETA: " + travelTime + " mins";
                    });

                    document.getElementById("assignedStp").innerText =
                        nearest.stp_name;
                }
            });

        },
        (error) => {
            alert("Unable to retrieve your location");
            console.error(error);
        }
    );
}