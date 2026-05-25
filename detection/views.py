from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
import json, math
from .models import Station, QueueStatus, UserProfile, OTPRecord, FuelTransaction

DETECTION_API_KEY = "fuelsense2024"

FUEL_PRICES = {
    'PETROL': 272,
    'DIESEL': 293,
    'KEROSENE': 187,
}

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def send_otp_sms(phone: str, otp: str):
    if getattr(settings, 'OTP_USE_TWILIO', False):
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        formatted = '+92' + phone.lstrip('0') if phone.startswith('0') else phone
        client.messages.create(
            body=f"FuelSense OTP: {otp}. Valid 5 minutes. Do not share.",
            from_=settings.TWILIO_FROM_NUMBER,
            to=formatted
        )
    else:
        print(f"\n{'='*40}\n[DEV OTP] {phone}  ->  {otp}\n{'='*40}\n")


def _tokens(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def _get_trend(station):
    statuses = QueueStatus.objects.filter(station=station).order_by('-timestamp')[:2]
    if len(statuses) < 2:
        return 'STABLE', 0
    current = statuses[0].vehicle_count
    previous = statuses[1].vehicle_count
    diff = current - previous
    if current > previous * 1.3:
        return 'GROWING', diff
    elif current < previous * 0.7:
        return 'SHRINKING', diff
    else:
        return 'STABLE', diff


def _seconds_ago(timestamp):
    if not timestamp:
        return None
    diff = (timezone.now() - timestamp).total_seconds()
    if diff < 60:
        return f"{int(diff)} seconds ago"
    elif diff < 3600:
        return f"{int(diff // 60)} min ago"
    else:
        return f"{int(diff // 3600)} hr ago"


@api_view(['POST'])
@permission_classes([AllowAny])
def send_otp(request):
    phone = request.data.get('phone', '').strip()
    if not phone or len(phone) < 10:
        return JsonResponse({'error': 'Valid phone number required'}, status=400)
    OTPRecord.objects.filter(phone=phone, is_used=False).update(is_used=True)
    otp = OTPRecord.generate_otp()
    OTPRecord.objects.create(phone=phone, otp=otp)
    try:
        send_otp_sms(phone, otp)
    except Exception as e:
        return JsonResponse({'error': f'SMS failed: {e}'}, status=500)
    return JsonResponse({
        'message': f'OTP sent to {phone}',
        'dev_otp': otp if settings.DEBUG else None
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_otp(request):
    phone = request.data.get('phone', '').strip()
    if not phone:
        return JsonResponse({'error': 'Phone required'}, status=400)
    try:
        p = UserProfile.objects.get(phone=phone)
        p.is_phone_verified = True
        p.save()
    except UserProfile.DoesNotExist:
        pass
    return JsonResponse({'verified': True, 'phone': phone})


@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    data      = request.data
    full_name = data.get('full_name', '').strip()
    phone     = data.get('phone', '').strip()
    cnic      = data.get('cnic', '').strip()
    password  = data.get('password', '')
    if not all([full_name, phone, cnic, password]):
        return JsonResponse({'error': 'All fields required'}, status=400)
    if len(password) < 8:
        return JsonResponse({'error': 'Password must be 8+ characters'}, status=400)
    if User.objects.filter(username=phone).exists():
        return JsonResponse({'error': 'Phone already registered'}, status=400)
    if UserProfile.objects.filter(cnic=cnic).exists():
        return JsonResponse({'error': 'CNIC already registered'}, status=400)
    parts = full_name.split(' ', 1)
    user  = User.objects.create_user(
        username=phone, password=password,
        first_name=parts[0], last_name=parts[1] if len(parts) > 1 else ''
    )
    UserProfile.objects.create(user=user, phone=phone, cnic=cnic, is_phone_verified=True)
    return JsonResponse({'message': 'Account created', 'user_id': user.id, **_tokens(user)}, status=201)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    phone    = request.data.get('phone', '').strip()
    password = request.data.get('password', '')
    user = authenticate(username=phone, password=password)
    if not user:
        return JsonResponse({'error': 'Invalid phone or password'}, status=401)
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({'error': 'Account incomplete'}, status=400)
    return JsonResponse({
        'user_id': user.id,
        'full_name': user.get_full_name(),
        'phone': profile.phone,
        'is_station_owner': profile.is_station_owner,
        'owned_station_id': profile.owned_station_id,
        **_tokens(user),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_profile(request):
    p = request.user.profile
    return JsonResponse({
        'user_id': request.user.id,
        'full_name': request.user.get_full_name(),
        'phone': p.phone,
        'is_station_owner': p.is_station_owner,
        'owned_station_id': p.owned_station_id,
    })


@csrf_exempt
def update_queue(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if data.get('api_key') != DETECTION_API_KEY:
        return JsonResponse({'error': 'Invalid API key'}, status=403)
    try:
        station = Station.objects.get(id=data['station_id'])
    except Station.DoesNotExist:
        return JsonResponse({'error': f"Station {data['station_id']} not found"}, status=404)
    count = int(data.get('vehicle_count', 0))
    qs    = QueueStatus.objects.create(station=station, vehicle_count=count)
    return JsonResponse({'status': 'ok', 'station': station.name, 'vehicles': count, 'congestion': qs.congestion_level})


@api_view(['GET'])
@permission_classes([AllowAny])
def traffic_status(request):
    stations = Station.objects.filter(is_active=True)
    result = []
    for s in stations:
        latest = QueueStatus.objects.filter(station=s).first()
        count  = latest.vehicle_count if latest else 0
        level  = latest.congestion_level if latest else 'GREEN'
        trend, diff = _get_trend(s)
        statuses = QueueStatus.objects.filter(station=s).order_by('-timestamp')[:2]
        prev_count = statuses[1].vehicle_count if len(statuses) >= 2 else count
        result.append({
            'id': s.id,
            'name': s.name,
            'lat': s.lat,
            'lng': s.lng,
            'address': s.address,
            'vehicles': count,
            'previous_vehicles': prev_count,
            'status': level,
            'trend': trend,
            'trend_diff': diff,
            'wait_minutes': (count * 2) // max(s.total_pumps, 1),
            'total_pumps': s.total_pumps,
            'last_updated': _seconds_ago(latest.timestamp if latest else None),
            'has_fuel': True,
            'fuel_prices': FUEL_PRICES,
        })
    return JsonResponse(result, safe=False)


@api_view(['GET'])
@permission_classes([AllowAny])
def nearby_stations(request):
    lat    = float(request.GET.get('lat', 33.6844))
    lng    = float(request.GET.get('lng', 73.0479))
    radius = float(request.GET.get('radius', 10))
    result = []
    for s in Station.objects.filter(is_active=True):
        dist = haversine(lat, lng, s.lat, s.lng)
        if dist <= radius:
            latest = QueueStatus.objects.filter(station=s).first()
            count  = latest.vehicle_count if latest else 0
            level  = latest.congestion_level if latest else 'GREEN'
            trend, diff = _get_trend(s)
            result.append({
                'id': s.id, 'name': s.name, 'lat': s.lat, 'lng': s.lng,
                'address': s.address, 'vehicles': count, 'status': level,
                'trend': trend, 'trend_diff': diff,
                'wait_minutes': (count * 2) // max(s.total_pumps, 1),
                'distance_km': round(dist, 2), 'total_pumps': s.total_pumps,
                'last_updated': _seconds_ago(latest.timestamp if latest else None),
                'has_fuel': True,
            })
    result.sort(key=lambda x: x['distance_km'])
    return JsonResponse(result, safe=False)


@api_view(['GET'])
@permission_classes([AllowAny])
def station_status(request, station_id):
    try:
        s = Station.objects.get(id=station_id)
    except Station.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    latest = QueueStatus.objects.filter(station=s).first()
    count  = latest.vehicle_count if latest else 0
    level  = latest.congestion_level if latest else 'GREEN'
    trend, diff = _get_trend(s)
    return JsonResponse({
        'id': s.id, 'name': s.name, 'vehicles': count, 'status': level,
        'trend': trend, 'trend_diff': diff,
        'wait_minutes': (count * 2) // max(s.total_pumps, 1),
        'last_updated': _seconds_ago(latest.timestamp if latest else None),
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def station_history(request, station_id):
    try:
        s = Station.objects.get(id=station_id)
    except Station.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    since   = timezone.now() - timedelta(hours=24)
    history = QueueStatus.objects.filter(station=s, timestamp__gte=since).order_by('timestamp')
    return JsonResponse({'station': s.name, 'data': [
        {'time': str(h.timestamp), 'count': h.vehicle_count, 'status': h.congestion_level}
        for h in history
    ]})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_overview(request, station_id):
    try:
        s = Station.objects.get(id=station_id)
    except Station.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    now         = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_qs  = QueueStatus.objects.filter(station=s, timestamp__gte=today_start)
    month_qs  = QueueStatus.objects.filter(station=s, timestamp__gte=month_start)
    today_txn = FuelTransaction.objects.filter(station=s, timestamp__gte=today_start)
    month_txn = FuelTransaction.objects.filter(station=s, timestamp__gte=month_start)
    hourly = []
    for hour in range(24):
        h_start = today_start + timedelta(hours=hour)
        h_end   = h_start + timedelta(hours=1)
        h_qs    = QueueStatus.objects.filter(station=s, timestamp__gte=h_start, timestamp__lt=h_end)
        avg     = (sum(q.vehicle_count for q in h_qs) // h_qs.count()) if h_qs.exists() else 0
        hourly.append({'hour': hour, 'avg_vehicles': avg})
    fuel_breakdown = {}
    for t in month_txn:
        fuel_breakdown[t.fuel_type] = fuel_breakdown.get(t.fuel_type, 0) + t.liters
    peak_hours = sorted(hourly, key=lambda x: x['avg_vehicles'], reverse=True)[:3]
    return JsonResponse({
        'station': s.name,
        'today': {
            'avg_vehicles': (sum(q.vehicle_count for q in today_qs) // today_qs.count()) if today_qs.exists() else 0,
            'revenue_pkr': round(sum(t.amount for t in today_txn), 2),
            'liters_dispensed': round(sum(t.liters for t in today_txn), 2),
        },
        'monthly': {
            'total_visits': month_qs.count(),
            'revenue_pkr': round(sum(t.amount for t in month_txn), 2),
            'liters_dispensed': round(sum(t.liters for t in month_txn), 2),
        },
        'hourly_congestion': hourly,
        'fuel_breakdown_liters': fuel_breakdown,
        'peak_hours': peak_hours,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_transactions(request, station_id):
    days  = int(request.GET.get('days', 7))
    since = timezone.now() - timedelta(days=days)
    try:
        s = Station.objects.get(id=station_id)
    except Station.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    txns = FuelTransaction.objects.filter(station=s, timestamp__gte=since)
    return JsonResponse({'transactions': [
        {'id': t.id, 'fuel_type': t.fuel_type, 'liters': t.liters,
         'price_per_liter': t.price_per_liter, 'amount': t.amount,
         'vehicle_reg': t.vehicle_reg, 'timestamp': str(t.timestamp)}
        for t in txns
    ], 'count': txns.count()})