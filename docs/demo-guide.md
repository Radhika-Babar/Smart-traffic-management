# Demonstration Guide

A guided run of about 15 minutes for the college evaluation. Each step names what to click, what to expect, and what it
proves.

## Before the evaluators arrive

1. Start Docker Desktop and wait until it reports that it is running.
2. In PowerShell:

   ```powershell
   cd E:\IOT-project\smart-traffic-management
   docker compose up -d --build
   ```

3. Open <http://localhost:5173> and wait until the top bar shows the green **MQTT** badge.
4. Open **Traffic Prediction** and confirm the model table is filled in. This takes about 30 seconds on a first start.
5. Keep a second window open for the MQTT step below.

If anything has been left in an odd state, go to **Simulation Lab** and click **Reset**. Stored history is kept. Reset
starts with empty roads, so allow a minute or two for traffic to build, or use 5x speed. A normal start pre-fills the roads.

## Opening statement

> "This is a software-based IoT traffic system. There is no physical hardware: the sensors and vehicles are simulated.
> Everything else is real, including the MQTT broker, the database, the adaptive algorithm and the machine learning.
> Everything runs on this laptop with no internet and no API keys."

Point out the **Simulated data** badge in the top bar, which stays on every screen.

## Step 1: The dashboard (1 minute)

Open **Overview**. Show the ten network metrics and live charts.

**Proves:** the dashboard reads live simulation data. The line under the title is computed from sensors and changes as
traffic changes.

## Step 2: Real MQTT on the wire (2 minutes)

This is the strongest proof that the IoT layer is real. In a second window, subscribe to the broker as an independent
client, completely outside the application:

```powershell
docker compose exec mosquitto mosquitto_sub -t "traffic/sensors/+/readings" -v
```

Sensor readings scroll past. Press `Ctrl+C`, then watch the adaptive algorithm's commands:

```powershell
docker compose exec mosquitto mosquitto_sub -t "traffic/signals/+/command" -v
```

Each command is an adaptive decision with its reason, sent back to a signal through the broker.

**Proves:** criteria 3 to 5. The algorithm only ever sees what arrives over MQTT.

## Step 3: The live map and adaptive decisions (3 minutes)

1. Open **Live Traffic Map**. Point out vehicle types, signals, and the blue squares that mark sensor sites.
2. Click **Central Junction**. On the **Decision** tab, read out the sentence explaining the latest decision, and compare
   it with the scores per direction.
3. Open the **Sensors** tab to show live readings. Then, in the scenario menu above the map, choose **Morning rush hour**.
4. Watch queues build and the road tint amber or red.
5. Open **Signal Control** and show the **Phase history**. Every green is followed by yellow and then all-red.

**Proves:** criteria 7 to 9. Decisions come from live data and always follow the safety sequence.

## Step 4: Emergency priority (2 minutes)

1. Set the speed to **2x** in **Simulation Lab**, then open **Emergency Management**.
2. Choose Ambulance, from Northgate Circle to Riverside Gate, and click **Dispatch ambulance**.
3. Watch it on the map. Signals ahead clear through yellow and all-red and then give it green, before it arrives.
4. When it finishes, its travel time appears in the **Response log**.

Say: *"Priority is requested, not forced. The controller still enforces minimum green, yellow and all-red."*

**Proves:** criterion 11.

## Step 5: Accident detection (2 minutes)

1. Open **Incidents**. Choose Major accident, Central Junction, East, and click **Inject incident**.
2. It appears as **Awaiting detection**.
3. Open the map to see the marker and the queue forming behind it.
4. Return to **Incidents**. It changes to **Detected**, with the time taken and the evidence.

Say: *"The detector cannot see the injected event. It only sees sensor readings, and it notices a queue that does not
reach the stop line. That pattern separates an obstruction from a normal signal queue."*

This takes about 30 seconds of simulated time. Set the speed to 5x if you want it sooner.

**Proves:** criterion 12.

## Step 6: Failure handling (2 minutes)

1. In **Simulation Lab**, click **Fail all sensors on this approach**.
2. Open **Alerts** to see the sensor failures. Open **IoT Sensors**, filter by Offline, and click **Restart** on one.
3. Now stop the broker while everything is running:

   ```powershell
   docker compose stop mosquitto
   ```

   Within seconds the top bar changes to **Local fallback**, an alert appears, and traffic keeps moving.

4. Start it again:

   ```powershell
   docker compose start mosquitto
   ```

   The badge returns to **MQTT** by itself within about 15 seconds.

**Proves:** the MQTT reconnect logic and the labelled fallback.

## Step 7: Prediction and analytics (2 minutes)

1. Open **Traffic Prediction**. Show actual against predicted on the hold-out period, and the model comparison.
   Point out the persistence baseline: *"We compare against the simplest possible forecast, and we report the measured
   difference, including where the simple linear model beats the random forest."*
2. Open **Analytics**, change a filter, then click **Export CSV**.

**Proves:** criteria 13 and 14.

## Step 8: Restart (1 minute)

```powershell
docker compose restart backend
```

The dashboard shows **Reconnecting**, then recovers by itself. Open **Analytics** to show that history is still there.

**Proves:** criterion 16.

## Likely questions

**Is the data real?** No, and the interface says so. Vehicles and sensors are simulated. The messaging, storage,
processing and algorithms are real software running on that simulated data.

**Why is only one direction green at a time?** Each approach is a single lane carrying left turns, so opposing greens
would conflict. This is called split phasing. It costs capacity but makes the safety rule simple and provable.

**How do you know conflicting greens can't happen?** The state machine has one active direction by construction, a fuzz
test checks the invariants through 25 randomised runs of hostile commands, the simulation asserts it on every tick in
tests, and the backend rejects any state report showing two moving directions.

**How much better is adaptive control?** See section 14 of the project report. Mean wait fell by about 59% in normal
traffic, 39% at rush hour and 10% in overload, in this simulation, against an unoptimised fixed-time rotation. A well-tuned
real fixed-time plan would be a harder baseline.

**Why does the random forest not always win?** Because it does not. It is selected per target by measured error on data
it never saw.

**What is the 0.95 R² worth?** Less than it sounds. The history is synthetic, and traffic five minutes from now resembles
traffic now, so the persistence baseline already scores above 0.93. Compare models with each other, not with 1.0.

## If something goes wrong

| Problem | Action |
| --- | --- |
| Simulation looks stuck | Check the top bar. If it says Resume, it is paused. |
| Screen state is confusing | Simulation Lab, then Reset. |
| No MQTT badge | `docker compose up -d mosquitto` |
| Page is blank | `docker compose ps`, then `docker compose up -d` |
| Docker fails on the day | Use the "Run without Docker" steps in the README. |
