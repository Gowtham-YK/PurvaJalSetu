console.log("JS loaded");

// Create map
const map = L.map("map").setView([12.9716, 77.5946], 11);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map);

let placeMarker = null;
let stpMarkers = [];


async function searchPlace() {

    const place = document.getElementById("placeInput").value;
    const requiredKLD = document.getElementById("kldInput").value;
    const quality = document.getElementById("qualityInput").value;
    const type = document.getElementById("typeInput").value;

    if (!place) return;

    try {

        // Call your upgraded backend
        const response = await fetch(
            `/api/search_place?place=${place}&required_kld=${requiredKLD}&quality=${quality}&type=${type}`
        );

        const data = await response.json();

        if (data.error) {
            alert(data.error);
            return;
        }

        const { latitude, longitude } = data.searched_location;

        map.setView([latitude, longitude], 14);

        if (placeMarker) map.removeLayer(placeMarker);

        placeMarker = L.marker([latitude, longitude])
            .addTo(map)
            .bindPopup(place)
            .openPopup();

        // Clear old STP markers
        stpMarkers.forEach(m => map.removeLayer(m));
        stpMarkers = [];

        data.stps_ranked.forEach(stp => {

            const marker = L.marker([stp.latitude, stp.longitude])
                .addTo(map)
                .bindPopup(
                    `<b>${stp.name}</b><br>
                     Distance: ${stp.distance_km} km<br>
                     Available: ${stp.available_capacity} KLD<br>
                     Utilization: ${stp.utilization_percent}%`
                );

            stpMarkers.push(marker);
        });

    } catch (err) {
        alert("Something went wrong");
        console.error(err);
    }
}



// Distance function
function distance(lat1, lon1, lat2, lon2) {

    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI/180;
    const dLon = (lon2 - lon1) * Math.PI/180;

    const a =
        Math.sin(dLat/2)**2 +
        Math.cos(lat1*Math.PI/180) *
        Math.cos(lat2*Math.PI/180) *
        Math.sin(dLon/2)**2;

    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}
