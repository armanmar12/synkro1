(function () {
    var container = document.getElementById("canvas-container");
    if (!container || typeof THREE === "undefined") {
        return;
    }

    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var isMobile = window.innerWidth < 768;
    var particleCount = reducedMotion ? 36 : (isMobile ? 60 : 100);
    var maxDistance = isMobile ? 24 : 30;

    var scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0xf8fafc, 0.015);

    var camera = new THREE.PerspectiveCamera(
        60,
        window.innerWidth / window.innerHeight,
        1,
        1000
    );
    camera.position.z = 120;

    var renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    var particlesGeometry = new THREE.BufferGeometry();
    var particlePositions = new Float32Array(particleCount * 3);
    var particleVelocities = [];

    for (var i = 0; i < particleCount; i += 1) {
        particlePositions[i * 3] = (Math.random() - 0.5) * 200;
        particlePositions[i * 3 + 1] = (Math.random() - 0.5) * 150;
        particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 100;
        particleVelocities.push({
            x: (Math.random() - 0.5) * 0.2,
            y: (Math.random() - 0.5) * 0.2,
            z: (Math.random() - 0.5) * 0.2
        });
    }

    particlesGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));

    var pointCloud = new THREE.Points(
        particlesGeometry,
        new THREE.PointsMaterial({
            color: 0x3b82f6,
            size: 2,
            transparent: true,
            opacity: 0.5,
            sizeAttenuation: true
        })
    );

    var linesGeometry = new THREE.BufferGeometry();
    var segmentsCount = particleCount * particleCount;
    var linePositions = new Float32Array(segmentsCount * 3);
    linesGeometry.setAttribute("position", new THREE.BufferAttribute(linePositions, 3));

    var linesMesh = new THREE.LineSegments(
        linesGeometry,
        new THREE.LineBasicMaterial({
            color: 0x94a3b8,
            transparent: true,
            opacity: 0.2
        })
    );

    var graphGroup = new THREE.Group();
    graphGroup.add(pointCloud);
    graphGroup.add(linesMesh);
    scene.add(graphGroup);

    if (!reducedMotion && typeof gsap !== "undefined" && typeof ScrollTrigger !== "undefined") {
        gsap.registerPlugin(ScrollTrigger);
        gsap.to(graphGroup.rotation, {
            y: Math.PI * 1.5,
            x: Math.PI * 0.2,
            ease: "none",
            scrollTrigger: {
                trigger: "body",
                start: "top top",
                end: "bottom bottom",
                scrub: 1
            }
        });
    }

    function animate() {
        var pPositions = pointCloud.geometry.attributes.position.array;
        var drawVertexIndex = 0;

        for (var i = 0; i < particleCount; i += 1) {
            pPositions[i * 3] += particleVelocities[i].x;
            pPositions[i * 3 + 1] += particleVelocities[i].y;
            pPositions[i * 3 + 2] += particleVelocities[i].z;

            if (Math.abs(pPositions[i * 3]) > 100) particleVelocities[i].x *= -1;
            if (Math.abs(pPositions[i * 3 + 1]) > 75) particleVelocities[i].y *= -1;
            if (Math.abs(pPositions[i * 3 + 2]) > 50) particleVelocities[i].z *= -1;
        }

        pointCloud.geometry.attributes.position.needsUpdate = true;

        for (var i = 0; i < particleCount; i += 1) {
            for (var j = i + 1; j < particleCount; j += 1) {
                var dx = pPositions[i * 3] - pPositions[j * 3];
                var dy = pPositions[i * 3 + 1] - pPositions[j * 3 + 1];
                var dz = pPositions[i * 3 + 2] - pPositions[j * 3 + 2];
                var dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

                if (dist < maxDistance) {
                    linePositions[drawVertexIndex++] = pPositions[i * 3];
                    linePositions[drawVertexIndex++] = pPositions[i * 3 + 1];
                    linePositions[drawVertexIndex++] = pPositions[i * 3 + 2];

                    linePositions[drawVertexIndex++] = pPositions[j * 3];
                    linePositions[drawVertexIndex++] = pPositions[j * 3 + 1];
                    linePositions[drawVertexIndex++] = pPositions[j * 3 + 2];
                }
            }
        }

        linesMesh.geometry.setDrawRange(0, drawVertexIndex / 3);
        linesMesh.geometry.attributes.position.needsUpdate = true;

        if (!reducedMotion) {
            graphGroup.rotation.y += 0.0005;
            graphGroup.rotation.x += 0.0002;
        }

        renderer.render(scene, camera);
        window.requestAnimationFrame(animate);
    }

    animate();

    window.addEventListener("resize", function () {
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
    });
})();
