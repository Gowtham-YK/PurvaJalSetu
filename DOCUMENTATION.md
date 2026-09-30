# Wastewater Matching System: Technical Documentation

## Executive Summary

This project implements a comprehensive **data-driven decision-support system** for optimizing the allocation of treated wastewater from Sewage Treatment Plants (STPs) to various buyers including industries, construction sites, agricultural users, and municipal facilities. The system employs multi-objective optimization algorithms, GIS-based spatial analysis, and interactive visualization to promote sustainable urban water management.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [System Architecture](#system-architecture)
3. [Technical Implementation](#technical-implementation)
4. [Optimization Algorithms](#optimization-algorithms)
5. [Spatial Analysis](#spatial-analysis)
6. [Data Models](#data-models)
7. [User Interface](#user-interface)
8. [Performance Metrics](#performance-metrics)
9. [Usage Guide](#usage-guide)
10. [Future Enhancements](#future-enhancements)

---

## 1. Project Overview

### Problem Statement

Urban water scarcity is a growing concern globally. Treated wastewater represents a valuable resource that can reduce freshwater dependency when properly allocated. However, matching STPs with potential buyers involves complex optimization considering:

- **Spatial constraints**: Distance and transportation feasibility
- **Capacity constraints**: STP availability and buyer demand
- **Quality requirements**: Treatment levels and water quality parameters
- **Economic factors**: Transportation and operational costs
- **Multi-objective optimization**: Balancing cost, distance, and satisfaction

### Solution Approach

The system implements:
1. **Greedy optimization algorithm** for fast, near-optimal matching
2. **Hungarian algorithm** for optimal assignment problems
3. **Haversine distance calculation** for accurate geographic distances
4. **Multi-criteria decision making** balancing cost, distance, and quality
5. **Interactive visualization** with map-based representation
6. **Comprehensive analytics** for decision support

### Key Features

✅ **Automated Matching**: Intelligent buyer-STP pairing
✅ **Spatial Analysis**: GIS-based distance and service area calculation
✅ **Cost Optimization**: Minimize transportation and total costs
✅ **Quality Matching**: Ensure water quality meets requirements
✅ **Visual Analytics**: Interactive map and dashboard
✅ **Performance Metrics**: Comprehensive KPIs and reporting
✅ **Scalable Design**: Handles varying numbers of STPs and buyers

---

## 2. System Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INTERFACE LAYER                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Map View    │  │  Table View  │  │ Analytics    │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                           │
┌─────────────────────────────────────────────────────────────┐
│                   OPTIMIZATION ENGINE                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Greedy     │  │  Hungarian   │  │ Multi-Obj    │     │
│  │  Algorithm   │  │  Algorithm   │  │    Scoring   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                           │
┌─────────────────────────────────────────────────────────────┐
│                 SPATIAL ANALYSIS MODULE                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Haversine   │  │  Transport   │  │   Service    │     │
│  │   Distance   │  │     Cost     │  │     Area     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                           │
┌─────────────────────────────────────────────────────────────┐
│                      DATA LAYER                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │     STPs     │  │    Buyers    │  │   Matches    │     │
│  │  (Sellers)   │  │  (Consumers) │  │  (Results)   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

**Frontend (React):**
- React 18+ with Hooks
- SVG-based map visualization
- Responsive CSS styling
- Lucide React icons

**Backend (Python):**
- NumPy for numerical computation
- Pandas for data manipulation
- SciPy for optimization
- Scikit-learn for ML preprocessing

---

## 3. Technical Implementation

### 3.1 Data Preprocessing

The system implements comprehensive data preprocessing:

```python
class DataPreprocessor:
    def normalize_features(self, data, features):
        """Normalize numerical features using StandardScaler"""
        
    def handle_missing_values(self, data, strategy='mean'):
        """Handle missing values with multiple strategies"""
        
    def validate_data(self, stps, buyers):
        """Validate input data for consistency and constraints"""
```

**Validation Checks:**
- Capacity constraints (available ≤ capacity)
- Valid geographic coordinates
- Positive demand and distance values
- Valid quality treatment levels

### 3.2 Data Models

#### STP (Sewage Treatment Plant)

```python
@dataclass
class STP:
    id: str                    # Unique identifier
    name: str                  # Plant name
    latitude: float            # Geographic coordinate
    longitude: float           # Geographic coordinate
    capacity: float            # Total capacity (KL/day)
    available: float           # Available capacity (KL/day)
    quality: WaterQuality      # Treatment quality
    operational_cost: float    # ₹ per KL
```

#### Buyer

```python
@dataclass
class Buyer:
    id: str                    # Unique identifier
    name: str                  # Buyer name
    type: str                  # Industry/Construction/Agriculture/Municipal
    latitude: float            # Geographic coordinate
    longitude: float           # Geographic coordinate
    demand: float              # Water demand (KL/day)
    quality_required: str      # Required treatment level
    max_distance: float        # Maximum acceptable distance (km)
    price_willingness: float   # ₹ per KL
```

#### WaterQuality

```python
@dataclass
class WaterQuality:
    bod: float                 # Biological Oxygen Demand (mg/L)
    cod: float                 # Chemical Oxygen Demand (mg/L)
    tss: float                 # Total Suspended Solids (mg/L)
    ph: float                  # pH level
    treatment_level: str       # Tertiary/Secondary/Primary
```

### 3.3 Quality Hierarchy

The system implements a quality hierarchy for matching:

```
Tertiary Treatment (Highest Quality)
    ├── BOD: ≤ 10 mg/L
    ├── COD: ≤ 50 mg/L
    ├── TSS: ≤ 10 mg/L
    └── Suitable for: Municipal, Agriculture, Industry, Construction

Secondary Treatment (Medium Quality)
    ├── BOD: ≤ 20 mg/L
    ├── COD: ≤ 100 mg/L
    ├── TSS: ≤ 20 mg/L
    └── Suitable for: Agriculture, Industry, Construction

Primary Treatment (Basic Quality)
    ├── BOD: ≤ 30 mg/L
    ├── COD: ≤ 150 mg/L
    ├── TSS: ≤ 30 mg/L
    └── Suitable for: Construction only
```

---

## 4. Optimization Algorithms

### 4.1 Greedy Matching Algorithm

**Approach:** Iteratively select the best buyer-STP pair based on multi-objective scoring.

**Algorithm Steps:**

```
1. Generate all feasible connections (buyer, STP pairs)
   - Check distance constraint: distance ≤ buyer.max_distance
   - Check quality constraint: stp.quality ≥ buyer.quality_required
   - Check availability: stp.available > 0

2. For each feasible connection, calculate:
   - Distance (Haversine formula)
   - Transport cost (distance × volume × rate with discounts)
   - Total cost (operational + transport)
   
3. Calculate multi-objective score:
   score = 10000 / (total_cost + distance × 10) + quality_bonus
   
   where quality_bonus = 100 (Tertiary)
                        50 (Secondary)
                        0 (Primary)

4. Sort connections by score (descending)

5. Greedy allocation:
   - For each connection in sorted order:
     - If buyer not yet allocated AND STP has availability:
       - Allocate min(buyer.demand, stp.available)
       - Update STP availability
       - Mark buyer as allocated

6. Return matches
```

**Complexity:** O(n × m + nm log(nm)) where n = buyers, m = STPs

**Advantages:**
- Fast execution
- Near-optimal solutions
- Handles large datasets
- Intuitive results

### 4.2 Hungarian Algorithm

**Approach:** Optimal assignment using the Hungarian (Munkres) algorithm.

**Algorithm Steps:**

```
1. Create cost matrix [n_buyers × n_stps]:
   cost[i][j] = total_cost + distance × weight + quality_penalty
   cost[i][j] = ∞ if infeasible

2. Apply Hungarian algorithm:
   - Find minimum cost assignment
   - Guarantees optimal solution for assignment problem

3. Extract matches from assignment matrix

4. Validate and calculate metrics for each match
```

**Complexity:** O(n³) using Hungarian algorithm

**Advantages:**
- Globally optimal for assignment
- Proven algorithm
- Balanced allocation

**Limitations:**
- Doesn't handle partial allocations well
- Computationally expensive for large datasets
- May not be practical for real-time systems

### 4.3 Multi-Objective Scoring

The system balances multiple objectives:

```python
Objective 1: Minimize Total Cost
    weight = high (primary consideration)
    
Objective 2: Minimize Distance
    weight = medium (environmental + reliability)
    
Objective 3: Maximize Quality Match
    weight = medium (future-proofing)
    
Objective 4: Maximize Demand Satisfaction
    weight = high (service level)
```

**Composite Score Formula:**

```
score = α × (1 / normalized_cost) 
      + β × (1 / normalized_distance)
      + γ × quality_match_score
      + δ × satisfaction_score

where: α + β + γ + δ = 1 (weights sum to 1)
```

---

## 5. Spatial Analysis

### 5.1 Haversine Distance Calculation

Calculates great-circle distance between two points on Earth's surface.

**Formula:**

```
a = sin²(Δlat/2) + cos(lat1) × cos(lat2) × sin²(Δlon/2)
c = 2 × atan2(√a, √(1−a))
distance = R × c

where:
    R = 6371 km (Earth's radius)
    Δlat = lat2 - lat1
    Δlon = lon2 - lon1
```

**Implementation:**

```python
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad = np.radians(lat1)
    lon1_rad = np.radians(lon1)
    lat2_rad = np.radians(lat2)
    lon2_rad = np.radians(lon2)
    
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    a = np.sin(dlat/2)**2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    
    return R * c
```

**Accuracy:** ±0.5% for distances < 100 km

### 5.2 Transportation Cost Model

The system implements a volume-based discount model:

```python
def calculate_transport_cost(distance, volume):
    base_rate = 2.0  # ₹ per KL per km
    
    if volume > 500:
        discount = 0.8  # 20% discount
    elif volume > 200:
        discount = 0.9  # 10% discount
    else:
        discount = 1.0  # No discount
    
    return base_rate * discount * distance
```

**Cost Components:**
- Fixed cost per KL
- Distance-proportional cost
- Volume-based economies of scale
- Infrastructure amortization

### 5.3 Service Area Generation

Creates circular service areas for visualization:

```python
def create_service_area(center_lat, center_lon, radius, points=32):
    """Generate polygon representing service area"""
    coords = []
    for i in range(points):
        angle = 2 * π * i / points
        dlat = (radius / 111.0) * cos(angle)
        dlon = (radius / (111.0 * cos(radians(center_lat)))) * sin(angle)
        coords.append((center_lat + dlat, center_lon + dlon))
    return coords
```

---

## 6. User Interface

### 6.1 Design Philosophy

The UI follows a **brutalist-tech aesthetic** with:
- Monospace fonts (Space Mono, IBM Plex Mono)
- Cyan/teal color scheme (#00e5ff primary)
- Dark gradient backgrounds
- Grid-based layouts
- Transparent panels with backdrop blur
- High-contrast data visualization

### 6.2 View Modes

#### Map View
- Interactive SVG-based map
- STP markers (circles)
- Buyer markers (squares)
- Flow lines showing allocations
- Click interactions for details
- Dynamic filtering

#### Table View
- Sortable columns
- Row highlighting on hover
- Compact data presentation
- Match satisfaction indicators
- Type-based filtering

#### Analytics View
- Supply vs. Demand charts
- Buyer type distribution
- Quality matching statistics
- Economic impact metrics
- Cost savings calculations

### 6.3 Interactive Features

**Map Interactions:**
- Click on connection lines to view match details
- Hover effects for enhanced visibility
- Legend for marker types
- Automatic bounds calculation

**Filtering:**
- Filter by buyer type (Industry, Construction, Agriculture, Municipal)
- Dynamic re-rendering
- Real-time statistics update

**Detail Panel:**
- Comprehensive match information
- Buyer and STP details
- Quality parameters
- Cost breakdown
- Satisfaction metrics

---

## 7. Performance Metrics

### 7.1 Key Performance Indicators (KPIs)

```python
Supply & Demand:
    - Total Demand (KL)
    - Total Supply (KL)
    - Allocated Volume (KL)
    - Demand Satisfaction (%)

Matching Performance:
    - Match Rate (%)
    - Number of Matched Buyers
    - Number of Unmatched Buyers
    - Average Satisfaction (%)

Logistics:
    - Average Distance (km)
    - Total Transport Distance (km)
    - Distance-weighted Volume (KL·km)

Economic:
    - Total Cost Savings (₹)
    - Average Cost per KL (₹/KL)
    - Cost Reduction vs. Freshwater (%)

Environmental:
    - Freshwater Conserved (ML)
    - CO₂ Emissions Reduction (tons)
    - Sustainability Score
```

### 7.2 Optimization Quality Metrics

```python
Solution Quality:
    - Optimality Gap (for Hungarian)
    - Convergence Time (seconds)
    - Computational Complexity
    
Constraint Satisfaction:
    - Distance Constraint Violations: 0
    - Capacity Constraint Violations: 0
    - Quality Constraint Violations: 0
    
Resource Utilization:
    - STP Utilization Rate (%)
    - Buyer Demand Coverage (%)
    - Capacity Allocation Efficiency (%)
```

---

## 8. Usage Guide

### 8.1 Python Backend

**Installation:**

```bash
pip install numpy pandas scipy scikit-learn
```

**Basic Usage:**

```python
from wastewater_matching_system import WastewaterMatchingSystem, DataGenerator

# Initialize system
system = WastewaterMatchingSystem()

# Generate data
generator = DataGenerator()
stps = generator.generate_stps(count=8)
buyers = generator.generate_buyers(count=15)

# Load data
system.load_data(stps, buyers)

# Run optimization
matches = system.run_optimization(algorithm='greedy')

# Print report
system.print_report()

# Export results
system.export_results('results.json')
```

**Custom Data Loading:**

```python
# Load from CSV
import pandas as pd

stps_df = pd.read_csv('stps.csv')
buyers_df = pd.read_csv('buyers.csv')

# Convert to objects
stps = [STP(**row) for _, row in stps_df.iterrows()]
buyers = [Buyer(**row) for _, row in buyers_df.iterrows()]

system.load_data(stps, buyers)
```

### 8.2 React Frontend

**Running the Application:**

```bash
# The React component is self-contained
# Include it in your React app

import WastewaterMatchingSystem from './wastewater_matching_system.jsx';

function App() {
  return <WastewaterMatchingSystem />;
}
```

**Customization:**

```javascript
// Adjust city center
const cityCenter = { lat: YOUR_LAT, lng: YOUR_LNG };

// Modify STP count
const stps = generateSTPs(10);  // Generate 10 STPs

// Change buyer count
const buyers = generateBuyers(20);  // Generate 20 buyers

// Filter by type
setFilterType('Industry');  // Show only industry buyers
```

---

## 9. Future Enhancements

### 9.1 Technical Enhancements

**Machine Learning Integration:**
- Demand forecasting using time series models
- Quality prediction using regression
- Anomaly detection for STP performance
- Clustering for buyer segmentation

**Advanced Optimization:**
- Multi-period planning (daily/weekly/monthly)
- Stochastic optimization for uncertainty
- Real-time dynamic reallocation
- Genetic algorithms for larger instances

**Spatial Features:**
- Road network integration (actual distances)
- Elevation consideration
- Pipeline cost modeling
- Restricted zones and regulations

### 9.2 Functional Enhancements

**Data Integration:**
- Real-time STP monitoring
- IoT sensor integration
- Weather data incorporation
- Water quality auto-testing

**Business Logic:**
- Contract management
- Pricing optimization
- Payment integration
- Scheduling system

**Reporting:**
- Automated report generation
- Email notifications
- Dashboard alerts
- Performance trending

### 9.3 Scalability Improvements

**Performance:**
- Database integration (PostgreSQL/MongoDB)
- Caching layer (Redis)
- API backend (FastAPI/Flask)
- Microservices architecture

**Multi-City Support:**
- City selection module
- Regional optimization
- Inter-city transfers
- Centralized dashboard

---

## 10. Research & References

### 10.1 Academic Foundation

**Optimization Theory:**
- Hungarian Algorithm (Kuhn, 1955)
- Multi-objective optimization (Pareto efficiency)
- Greedy algorithms complexity analysis

**Spatial Analysis:**
- Haversine formula for great-circle distance
- GIS-based resource allocation
- Service area analysis

**Water Management:**
- Wastewater reuse guidelines (WHO, EPA)
- Treatment quality standards (IS 13496)
- Sustainable urban water management

### 10.2 Standards Compliance

**Water Quality:**
- IS 13496:1992 (Wastewater for irrigation)
- CPCB guidelines for reuse
- WHO guidelines for safe use

**GIS Standards:**
- WGS84 coordinate system
- GeoJSON format support
- Standard map projections

---

## 11. Conclusion

This wastewater matching system demonstrates how modern optimization techniques, spatial analysis, and interactive visualization can be combined to solve complex urban resource allocation problems. The system provides:

✅ **Automated decision support** for water resource managers
✅ **Multi-objective optimization** balancing cost, distance, and quality
✅ **Transparent matching** with detailed justifications
✅ **Scalable architecture** for real-world deployment
✅ **Visual analytics** for stakeholder communication

The implementation showcases best practices in:
- Algorithm design and complexity analysis
- Data modeling and validation
- User interface design
- Performance optimization
- Sustainable resource management

---

## Appendix A: Mathematical Formulations

### Cost Function

```
minimize: Σᵢ Σⱼ (xᵢⱼ × (cⱼ + tᵢⱼ))

where:
    xᵢⱼ = volume allocated from STP j to buyer i
    cⱼ = operational cost of STP j
    tᵢⱼ = transport cost from STP j to buyer i
```

### Constraints

```
Subject to:
    (1) Σⱼ xᵢⱼ ≤ dᵢ           ∀i (demand satisfaction)
    (2) Σᵢ xᵢⱼ ≤ sⱼ           ∀j (capacity constraint)
    (3) dᵢⱼ ≤ Dᵢ              ∀i,j (distance constraint)
    (4) qⱼ ≥ Qᵢ               ∀i,j (quality constraint)
    (5) xᵢⱼ ≥ 0               ∀i,j (non-negativity)

where:
    dᵢ = demand of buyer i
    sⱼ = supply available at STP j
    dᵢⱼ = distance between buyer i and STP j
    Dᵢ = maximum acceptable distance for buyer i
    qⱼ = quality level of STP j
    Qᵢ = required quality level for buyer i
```

---

**Document Version:** 1.0  
**Last Updated:** February 8, 2026  
**Author:** Claude AI  
**License:** MIT
